"""OpenAI-compatible Client that runs one Hermes Turn through the Claude Agent SDK.

Each ``chat.completions.create()`` call starts a short-lived Runtime (the Claude Code
process the SDK launches under the Subscriber's own Subscription Login), sends the whole
Hermes conversation as one prompt, and returns the minimal OpenAI-client shape Hermes reads
(``choices[0].message.content`` / ``.tool_calls``, ``usage``).

Tool contract (see CONTEXT.md and docs/adr/0001):
- Every Hermes Tool is exposed to the Runtime through the Tool Bridge, an in-process MCP
  server named ``hermes`` (so the Runtime sees ``mcp__hermes__<name>``).
- Runtime Built-in Tools are disabled up front (``tools=[]``), so the Runtime can only
  propose Hermes Tools.
- ``permission_mode="dontAsk"`` makes the Runtime deny every Tool Proposal locally; the
  proposal comes back to Hermes as an OpenAI ``tool_calls`` entry and Hermes executes it.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import json
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from agent.acp_openai_bridge import build_openai_tool_call, completion_to_stream_chunks

from . import runtime_upgrade

logger = logging.getLogger(__name__)

MARKER_BASE_URL = "claude-agent-sdk://local"
MCP_SERVER_NAME = "hermes"
MCP_TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"
DENY_MESSAGE = "Tool execution is unavailable in this environment; the host executes tools."
# Hermes effort level meaning "thinking off". The profile maps the rest of the ladder onto the
# SDK vocabulary; this one value becomes a thinking config instead of an effort.
THINKING_OFF = "none"
_DEFAULT_TIMEOUT_SECONDS = 900.0
_CANCEL_GRACE_SECONDS = 10.0
_DATA_URL_RE = re.compile(r"^data:(?P<media>[\w/+.-]+);base64,(?P<data>.+)$", re.DOTALL)
_LOGIN_HINT = "Log in to Claude Code with your subscription first: run `claude login`."

_PROMPT_TRAILER = (
    "Continue the conversation from the latest message above. Historical tool calls and "
    "results are shown for context only; to act, call the provided tools."
)


# ── Message rendering ───────────────────────────────────────────────────────────────────


def _effective_timeout(timeout: Any) -> float:
    """Normalise a float or httpx.Timeout-like object to wall-clock seconds (largest component
    wins). Same rule as Hermes' ``agent/copilot_acp_client.py``, copied because that symbol is
    private to core."""
    if isinstance(timeout, (int, float)):
        return float(timeout)
    candidates = [getattr(timeout, attr, None) for attr in ("read", "write", "connect", "pool", "timeout")]
    return max((float(v) for v in candidates if isinstance(v, (int, float))), default=_DEFAULT_TIMEOUT_SECONDS)


def _image_block(url: str) -> dict[str, Any] | None:
    """Anthropic image block from a base64 data URL; remote URLs are passed as url sources."""
    if m := _DATA_URL_RE.match(url or ""):
        return {"type": "image", "source": {"type": "base64", "media_type": m.group("media"), "data": m.group("data")}}
    if url.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _content_blocks(content: Any) -> tuple[list[dict[str, Any]], bool]:
    """OpenAI message content → Anthropic content blocks; second value = has non-blank text."""
    if content is None:
        return [], False
    if isinstance(content, str):
        return ([{"type": "text", "text": content}] if content else []), bool(content.strip())
    if not isinstance(content, list):
        text = json.dumps(content, ensure_ascii=False)
        return [{"type": "text", "text": text}], True
    blocks: list[dict[str, Any]] = []
    has_text = False
    for part in content:
        if isinstance(part, str):
            blocks.append({"type": "text", "text": part})
            has_text = has_text or bool(part.strip())
        elif not isinstance(part, dict):
            continue
        elif part.get("type") == "text":
            text = str(part.get("text") or "")
            blocks.append({"type": "text", "text": text})
            has_text = has_text or bool(text.strip())
        elif part.get("type") == "image_url":
            url = part.get("image_url", {}).get("url") if isinstance(part.get("image_url"), dict) else part.get("image_url")
            if block := _image_block(str(url or "")):
                blocks.append(block)
        elif "text" in part:
            blocks.append({"type": "text", "text": str(part["text"])})
            has_text = True
    return blocks, has_text


def _tool_call_line(tc: Any) -> str:
    fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
    name = (fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)) or "?"
    args = (fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", None)) or "{}"
    call_id = (tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)) or "?"
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False)
    return f"[tool call {name} id={call_id} args={args}]"


def split_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """``(system_prompt, prompt_blocks)``: system messages become the Runtime system prompt; the
    rest is flattened into one user message with USER / ASSISTANT / TOOL RESULT sections."""
    system_parts: list[str] = []
    blocks: list[dict[str, Any]] = []

    def push_text(text: str) -> None:
        if blocks and blocks[-1]["type"] == "text":
            blocks[-1]["text"] += text
        else:
            blocks.append({"type": "text", "text": text})

    def push_section(label: str, content: Any, *, extra_lines: list[str] | None = None) -> None:
        push_text(("\n\n" if blocks else "") + label + "\n")
        parts, has_text = _content_blocks(content)
        for part in parts:
            if part["type"] == "text":
                push_text(part["text"])
            else:
                blocks.append(part)
        if not has_text and any(p["type"] == "image" for p in parts):
            push_text("(see attached image)")
        for line in extra_lines or []:
            push_text(("\n" if has_text else "") + line)

    for message in (m for m in messages if isinstance(m, dict)):
        role = str(message.get("role") or "").strip().lower()
        content = message.get("content")
        if role == "system":
            text = "\n".join(p["text"] for p in _content_blocks(content)[0] if p["type"] == "text")
            if text.strip():
                system_parts.append(text)
        elif role == "assistant":
            calls = [_tool_call_line(tc) for tc in (message.get("tool_calls") or [])]
            push_section("ASSISTANT:", content, extra_lines=calls)
        elif role == "tool":
            push_section(f"TOOL RESULT (id={message.get('tool_call_id') or '?'}):", content)
        else:
            push_section("USER:", content)

    push_text(f"\n\n{_PROMPT_TRAILER}")
    return "\n\n".join(system_parts), blocks


# ── Tool Bridge ─────────────────────────────────────────────────────────────────────────


def _hermes_tool_name(runtime_name: str) -> str:
    """Map a Runtime tool name back to the Hermes Tool name (strip the Tool Bridge prefix)."""
    return runtime_name[len(MCP_TOOL_PREFIX):] if runtime_name.startswith(MCP_TOOL_PREFIX) else runtime_name


def _build_tool_bridge(tools: list[dict[str, Any]] | None) -> Any | None:
    """In-process MCP server exposing Hermes tool schemas; handlers only ever deny (Hermes executes)."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    async def _deny(_args: Any) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": DENY_MESSAGE}], "is_error": True}

    sdk_tools = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) else None
        name = str((fn or {}).get("name") or "").strip()
        if not name:
            continue
        schema = fn.get("parameters") or {"type": "object", "properties": {}}
        sdk_tools.append(tool(name, str(fn.get("description") or f"Hermes {name} tool"), schema)(_deny))
    if not sdk_tools:
        return None
    return create_sdk_mcp_server(MCP_SERVER_NAME, version="0.1.0", tools=sdk_tools)


# ── Turn execution ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _TurnRequest:
    """Everything one Turn needs, in Hermes terms."""

    model: str | None
    system_prompt: str
    prompt_blocks: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    cwd: str
    cli_path: str | None
    reasoning_effort: str | None  # SDK vocabulary, or THINKING_OFF


@dataclass
class _TurnResult:
    text_parts: list[str] = field(default_factory=list)
    thinking_parts: list[str] = field(default_factory=list)
    tool_calls: list[Any] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""

    @property
    def text(self) -> str:
        return "".join(self.text_parts).strip()

    @property
    def thinking(self) -> str:
        return "".join(self.thinking_parts).strip()


class RuntimeFailure(RuntimeError):
    """A Turn the Runtime failed or refused. ``status_code`` carries the HTTP status behind a Runtime
    rejection: Hermes' error classifier keys on it, and without one every failure reads as a
    transient outage that is worth retrying."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ``AssistantMessage.error`` kinds no retry can fix, as the HTTP status Hermes classifies on
# (400 → format_error, 404 → model_not_found: both skip the retries and go to fallback).
_REJECTION_STATUS = {"invalid_request": 400, "model_not_found": 404}
# The API refusing a Runtime older than the model (e.g. Claude Code 2.1.276 for claude-opus-5-5).
_RUNTIME_TOO_OLD_RE = re.compile(r"(?:Claude Code \S+ )?does not support this model; version \S+ or newer is required")
# Hermes classifies "unsupported model" as model_not_found, whose lead copy ("Pick a different model
# with /model") is the first upgrade step; the generic 400 copy points at /new instead.
_UNSUPPORTED_MODEL = "unsupported model for this Runtime"
# Models every Runtime this plugin supports can run: where the upgrade steps park the Subscriber.
_UPGRADE_PARKING_MODELS = ("claude-sonnet-5", "claude-haiku-4-5-20251001")
# Hermes shows only this many characters of the error under "Provider said:".
_PROVIDER_SAID_LIMIT = 500


def _runtime_error(detail: str, status_code: int | None = None) -> RuntimeFailure:
    """Runtime-side failure surfaced to Hermes; authentication failures point at ``claude login``."""
    lowered = detail.lower()
    if any(k in lowered for k in ("authentication", "not logged in", "login", "unauthorized", "401")):
        return RuntimeFailure(f"claude-agent-sdk: {detail}. {_LOGIN_HINT}", status_code)
    return RuntimeFailure(f"claude-agent-sdk: {detail}", status_code)


def _chat_upgrade_steps(model: str | None) -> str:
    """How to upgrade the bundled Runtime from a chat app (Hermes is often reached only through one):
    park on a model the old Runtime runs, have Hermes run the upgrade with its terminal tool, start over.
    The command travels as one tap-to-copy block, worded so neither the Subscriber nor the model
    "fixes" it: `-P` upgrades the SDK alone, while `-U` also moves packages Hermes pins."""
    parking = next(m for m in _UPGRADE_PARKING_MODELS if m != model)
    command = f"uv pip install --python {sys.executable} -P claude-agent-sdk claude-agent-sdk"
    return (f"To fix:\n1. /model {parking} --provider claude-agent-sdk\n2. Send Hermes:\n"
            f"```\nRun exactly, package name twice on purpose, no -U: {command}\n```\n3. /new")


class RuntimeTooOld(RuntimeFailure):
    """The bundled Runtime is older than the requested model; upgrading claude-agent-sdk fixes it.
    Its message is the manual fix, plus ``note`` on what the automatic upgrade did."""

    def __init__(self, reason: str, model: str | None, status_code: int | None, *, sdk_version: str | None,
                 note: str = "", blocked_by: tuple[str, ...] = ()) -> None:
        self.reason, self.model, self.sdk_version = reason, model, sdk_version
        self.note, self.blocked_by = note, blocked_by
        super().__init__(self._message(), status_code)

    def _message(self) -> str:
        head = f"claude-agent-sdk: {_UNSUPPORTED_MODEL}"
        if self.blocked_by:  # a newer SDK needs newer shared packages: upgrading it would break Hermes' pins
            return (f"{head}: {self.reason}. A newer claude-agent-sdk also needs newer "
                    f"{', '.join(self.blocked_by)} than Hermes pins: update Hermes first, or pick another model "
                    "with /model.")
        note = f" Auto-upgrade: {self.note}." if self.note else ""
        steps = _chat_upgrade_steps(self.model)
        # On a long interpreter path, the steps matter more than why, and why more than the note.
        candidates = (f"{head}: {self.reason}.{note}\n{steps}", f"{head}.{note}\n{steps}", f"{head}.\n{steps}")
        return next((c for c in candidates if len(c) <= _PROVIDER_SAID_LIMIT), candidates[-1])

    def after_upgrade(self, outcome: runtime_upgrade.Outcome) -> RuntimeTooOld:
        """The same rejection, reporting an automatic upgrade that did not happen."""
        return RuntimeTooOld(self.reason, self.model, self.status_code, sdk_version=self.sdk_version,
                             note=outcome.detail, blocked_by=outcome.blocked_by)


def _installed_sdk_version() -> str | None:
    try:
        return importlib.metadata.version(runtime_upgrade.PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return None


def _assistant_error(kind: str, content: list[Any], *, bundled_runtime: bool, model: str | None) -> RuntimeFailure:
    """The Runtime's synthetic error message → an error that keeps its explanation (the only place
    the Runtime says *why*) and its HTTP status."""
    from claude_agent_sdk import TextBlock

    text = " ".join(b.text.strip() for b in content if isinstance(b, TextBlock) and b.text.strip())
    status = _REJECTION_STATUS.get(kind)
    if not (m := _RUNTIME_TOO_OLD_RE.search(text)):
        return _runtime_error(f"Runtime error '{kind}'" + (f": {text}" if text else ""), status)
    if not bundled_runtime:  # the Subscriber's own `claude`: its advice (`claude update`) is the fix
        return _runtime_error(f"{_UNSUPPORTED_MODEL}: {text[m.start():]}", status)
    # The Runtime's own advice (`claude update`) never reaches the copy bundled in the SDK.
    return RuntimeTooOld(m.group(0), model, status, sdk_version=_installed_sdk_version())


def _check_subscription_login(init_data: dict[str, Any]) -> None:
    """Compliant = the Runtime authenticates with the Subscription Login and nothing else.
    Fail closed: an unknown or missing source is refused, not assumed."""
    source = init_data.get("apiKeySource")
    if source == "none":
        return
    if source is None:
        raise RuntimeError(
            "claude-agent-sdk: the Runtime did not report how it authenticates; refusing to run. " + _LOGIN_HINT)
    raise RuntimeError(
        f"claude-agent-sdk: the Runtime would authenticate with an API key ({source}), which is billed "
        "pay-per-token, not against the Claude subscription. Unset ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN "
        "for Hermes, or use the built-in 'anthropic' provider instead.")


async def _run_turn_async(request: _TurnRequest) -> _TurnResult:
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, StreamEvent, SystemMessage, TextBlock,
        ThinkingBlock, ToolUseBlock)

    bridge = _build_tool_bridge(request.tools)
    thinking_off = request.reasoning_effort == THINKING_OFF
    options = ClaudeAgentOptions(
        model=request.model or None,
        tools=[],  # No Runtime Built-in Tools: only Hermes Tools via the Tool Bridge.
        mcp_servers={MCP_SERVER_NAME: bridge} if bridge else {},
        permission_mode="dontAsk",  # Every Tool Proposal is denied inside the Runtime.
        system_prompt=request.system_prompt,
        setting_sources=[],  # Ignore ~/.claude settings, CLAUDE.md, hooks.
        strict_mcp_config=True,  # Ignore ~/.claude.json / .mcp.json servers.
        include_partial_messages=True,  # message_start/message_delta/message_stop carry usage + turn end.
        cwd=request.cwd,
        cli_path=request.cli_path,
        effort=None if thinking_off else (request.reasoning_effort or None),
        thinking={"type": "disabled"} if thinking_off else None,
    )

    async def _prompt():
        # Streaming-input form: content blocks (text + images) rather than a bare string.
        yield {"type": "user", "message": {"role": "user", "content": request.prompt_blocks},
               "parent_tool_use_id": None, "session_id": "hermes"}

    result = _TurnResult()
    seen_tool_ids: set[str] = set()
    # The slot keeps an automatic upgrade from replacing the bundled binary under a live Runtime.
    with runtime_upgrade.runtime_slot():
        async with ClaudeSDKClient(options=options) as client:
            await client.query(_prompt())
            async for message in client.receive_messages():
                if isinstance(message, SystemMessage):
                    if message.subtype == "init":
                        _check_subscription_login(message.data or {})
                    continue
                if isinstance(message, StreamEvent):
                    event = message.event or {}
                    etype = event.get("type")
                    if etype == "message_start":
                        result.usage.update((event.get("message") or {}).get("usage") or {})
                    elif etype == "message_delta":
                        result.usage.update(event.get("usage") or {})
                    elif etype == "message_stop" and result.tool_calls:
                        # Assistant turn ended on a Tool Proposal. Stop here, before the Runtime denies it
                        # locally and spends another API round-trip reacting to that denial.
                        break
                    continue
                if isinstance(message, AssistantMessage):
                    if message.error:
                        raise _assistant_error(message.error, message.content,
                                               bundled_runtime=request.cli_path is None, model=request.model)
                    result.model = message.model or result.model
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            result.text_parts.append(block.text)
                        elif isinstance(block, ThinkingBlock):
                            result.thinking_parts.append(block.thinking)
                        elif isinstance(block, ToolUseBlock) and block.id not in seen_tool_ids:
                            seen_tool_ids.add(block.id)
                            result.tool_calls.append(build_openai_tool_call(
                                call_id=block.id, name=_hermes_tool_name(block.name),
                                arguments=json.dumps(block.input or {}, ensure_ascii=False)))
                    continue
                if isinstance(message, ResultMessage):
                    if message.is_error and not (result.text or result.tool_calls):
                        raise _runtime_error(str(message.result or message.subtype))
                    break
    return result


async def _run_turn(request: _TurnRequest) -> _TurnResult:
    """One Turn. When the bundled Runtime is too old for the model, upgrade claude-agent-sdk once
    (see ``runtime_upgrade`` for the guardrails) and run the Turn again on the new Runtime: the SDK
    resolves its bundled binary per Turn, so no restart is needed."""
    try:
        return await _run_turn_async(request)
    except RuntimeTooOld as too_old:
        outcome = await asyncio.to_thread(runtime_upgrade.upgrade, too_old.sdk_version)
        if not outcome.upgraded:
            raise too_old.after_upgrade(outcome) from None
    try:
        return await _run_turn_async(request)
    except RuntimeTooOld as still:
        failed = runtime_upgrade.Outcome(False, f"upgraded to {outcome.detail}, still too old")
        raise still.after_upgrade(failed) from None


class _TurnRunner:
    """Runs one Turn on a private thread + event loop (Hermes may call us from inside a loop), and
    can cancel it from any thread. Cancelling the task unwinds ``async with ClaudeSDKClient`` so
    the Runtime process is disconnected and terminated. ``on_exit`` fires on the Turn thread once
    it is done, however it ended."""

    def __init__(self, request: _TurnRequest, timeout_seconds: float,
                 on_exit: Callable[[_TurnRunner], None] | None = None) -> None:
        self._request = request
        self._timeout_seconds = timeout_seconds
        self._on_exit = on_exit
        self._loop = asyncio.new_event_loop()
        self._task: asyncio.Task | None = None
        self._outcome: dict[str, Any] = {}
        self._timed_out = False
        self._thread = threading.Thread(target=self._runner, name="claude-agent-sdk-turn", daemon=True)
        # Armed in start(): the deadline holds even if nobody ever joins the Turn.
        self._deadline = threading.Timer(timeout_seconds, self._on_deadline)
        self._deadline.daemon = True

    def _runner(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._task = self._loop.create_task(_run_turn(self._request))
            self._outcome["value"] = self._loop.run_until_complete(self._task)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            self._outcome["error"] = exc
        finally:
            self._deadline.cancel()
            self._loop.close()
            self._task = None
            if self._on_exit is not None:
                self._on_exit(self)

    def _on_deadline(self) -> None:
        self._timed_out = True
        self.cancel()

    def cancel(self) -> None:
        """Thread-safe: ask the Turn to stop and the Runtime to go away."""
        if self._loop.is_closed():
            return

        def _cancel() -> None:
            if self._task is not None:
                self._task.cancel()

        with contextlib.suppress(RuntimeError):  # loop closed between the check and the call
            self._loop.call_soon_threadsafe(_cancel)

    def start(self) -> None:
        try:
            self._thread.start()
        except BaseException:
            self._loop.close()
            raise
        self._deadline.start()

    def join(self) -> _TurnResult:
        """Wait for the Turn; past the deadline it is cancelled (the Runtime is terminated) and
        ``TimeoutError`` is raised."""
        self._thread.join(self._timeout_seconds + _CANCEL_GRACE_SECONDS)
        if self._thread.is_alive():
            # Stays registered on the client (until the thread exits) so close() can cancel it again.
            logger.warning("claude-agent-sdk: Runtime did not stop within %.0fs after cancel", _CANCEL_GRACE_SECONDS)
            self._timed_out = True
        if self._timed_out:
            raise TimeoutError(f"claude-agent-sdk: Turn exceeded {self._timeout_seconds:.0f}s.")
        if "error" in self._outcome:
            error = self._outcome["error"]
            if isinstance(error, asyncio.CancelledError):
                raise RuntimeError("claude-agent-sdk: Turn cancelled (client closed).") from None
            raise error
        return self._outcome["value"]


def _in_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class _PendingCompletion:
    """``create()`` result when the caller's thread has a running event loop, where sync and async
    consumers cannot be told apart up front (Hermes' async auxiliary path awaits it; a sync caller
    inside a loop reads it directly). The Turn is already running on its own thread; whichever
    way the result is consumed first joins it:

    - ``await`` joins on a worker thread, so the caller's loop keeps running, and yields the
      complete response even under ``stream=True`` (Hermes' async path accepts that, and its
      ``StreamChunks`` is sync-iterable only).
    - Attribute access / iteration joins on the calling thread and delegates to the completion,
      or to ``StreamChunks`` under ``stream=True``, exactly as the sync ``create()`` returns.

    Consequently a Turn failure (timeout, login guard, Runtime error) surfaces at the first
    consumption rather than from ``create()`` itself; the Turn's deadline is enforced either way."""

    __slots__ = ("_join", "_cancel", "_stream", "_lock", "_outcome", "_chunks")

    def __init__(self, join: Callable[[], SimpleNamespace], cancel: Callable[[], None], *, stream: bool) -> None:
        self._join: Callable[[], SimpleNamespace] | None = join
        self._cancel = cancel
        self._stream = stream
        self._lock = threading.Lock()
        self._outcome: dict[str, Any] = {}  # "value" or "error", filled once; later reads replay it
        self._chunks: Any = None

    def _joined(self) -> SimpleNamespace:
        with self._lock:
            if self._join is not None:
                try:
                    self._outcome["value"] = self._join()
                except BaseException as exc:  # noqa: BLE001 - replayed to every consumer
                    self._outcome["error"] = exc
                finally:
                    self._join = None  # drop the runner (and the request's image payloads) once settled
            if "error" in self._outcome:
                raise self._outcome["error"]
            return self._outcome["value"]

    async def _awaited(self) -> SimpleNamespace:
        try:
            return await asyncio.to_thread(self._joined)
        except asyncio.CancelledError:
            self._cancel()  # the awaiting task is going away: stop the Turn and its Runtime too
            raise

    def __await__(self):
        return self._awaited().__await__()

    def _sync_value(self) -> Any:
        completion = self._joined()
        if not self._stream:
            return completion
        if self._chunks is None:
            self._chunks = completion_to_stream_chunks(completion)
        return self._chunks

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._sync_value(), name)

    def __iter__(self):
        return iter(self._sync_value())

    def __len__(self) -> int:
        return len(self._sync_value())

    def __getitem__(self, index: Any) -> Any:
        return self._sync_value()[index]

    def __bool__(self) -> bool:
        return bool(self._sync_value())

    @property
    def __dict__(self) -> dict[str, Any]:  # ``vars(response)`` (generic serialisers) sees the completion
        return vars(self._sync_value())

    def __repr__(self) -> str:
        return f"<_PendingCompletion stream={self._stream} joined={bool(self._outcome)}>"


# ── OpenAI-shaped client ────────────────────────────────────────────────────────────────


class ClaudeAgentSDKClient:
    """Minimal OpenAI-client-compatible facade over the Claude Agent SDK."""

    # Declared for agent/auxiliary_client.py: complete client, never re-dispatch through a wire
    # adapter, and ``chat.completions.create`` is awaitable from async code (Hermes then uses this
    # client as-is on its async auxiliary path — see ``_PendingCompletion``).
    HERMES_SKIP_TRANSPORT_WRAP = True
    HERMES_SKIP_ASYNC_WRAP = True

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, cwd: str | None = None,
                 cli_path: str | None = None, timeout: Any = None, **_: Any):
        self.api_key = api_key or "claude-agent-sdk"  # placeholder: the Runtime owns auth
        self.base_url = base_url or MARKER_BASE_URL
        self._cwd = str(Path(cwd or os.getcwd()).resolve())
        # Optional: point the SDK at a specific Claude Code binary instead of its bundled one.
        self._cli_path = cli_path or os.getenv("HERMES_CLAUDE_AGENT_SDK_CLI", "").strip() or None
        self._default_timeout = timeout
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create_chat_completion))
        # Same semantics as Hermes' ACP client: True between Turns after close(); the next Turn resets it.
        self.is_closed = False
        self._active_turns: set[_TurnRunner] = set()
        self._active_turns_lock = threading.Lock()

    def close(self) -> None:
        """Stop every in-flight Turn and its Runtime. The client stays usable for later Turns."""
        self.is_closed = True
        with self._active_turns_lock:
            runners = list(self._active_turns)
        for runner in runners:
            runner.cancel()

    def _forget_turn(self, runner: _TurnRunner) -> None:
        with self._active_turns_lock:
            self._active_turns.discard(runner)

    def _start_turn(self, request: _TurnRequest, timeout_seconds: float) -> _TurnRunner:
        runner = _TurnRunner(request, timeout_seconds, on_exit=self._forget_turn)
        with self._active_turns_lock:
            self._active_turns.add(runner)
        self.is_closed = False
        try:
            runner.start()
        except BaseException:
            self._forget_turn(runner)
            raise
        return runner

    def _create_chat_completion(
        self, *, model: str | None = None, messages: list[dict[str, Any]] | None = None, timeout: Any = None,
        tools: list[dict[str, Any]] | None = None, tool_choice: Any = None, stream: bool = False,
        reasoning_effort: str | None = None, **_: Any,
    ) -> Any:
        """Run one Turn. From a plain thread this blocks and returns the completion (``StreamChunks``
        under ``stream=True``); from a thread with a running loop it returns a ``_PendingCompletion``
        that serves both ``await create(...)`` and direct sync reads."""
        del tool_choice  # The Runtime decides; Hermes' hint is not forwarded yet.
        system_prompt, prompt_blocks = split_messages(messages or [])
        request = _TurnRequest(
            model=model, system_prompt=system_prompt, prompt_blocks=prompt_blocks, tools=tools, cwd=self._cwd,
            cli_path=self._cli_path, reasoning_effort=reasoning_effort)
        runner = self._start_turn(request, _effective_timeout(timeout if timeout is not None else self._default_timeout))

        def join() -> SimpleNamespace:
            return self._to_completion(runner.join(), model)

        if _in_running_loop():
            return _PendingCompletion(join, runner.cancel, stream=stream)
        completion = join()
        return completion_to_stream_chunks(completion) if stream else completion

    @staticmethod
    def _to_completion(turn: _TurnResult, model: str | None) -> SimpleNamespace:
        usage = turn.usage
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        prompt_tokens = int(usage.get("input_tokens") or 0) + cache_read + cache_write
        completion_tokens = int(usage.get("output_tokens") or 0)

        message = SimpleNamespace(
            content=turn.text or None, tool_calls=turn.tool_calls or None,
            reasoning=turn.thinking or None, reasoning_content=turn.thinking or None, reasoning_details=None,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="tool_calls" if turn.tool_calls else "stop")],
            usage=SimpleNamespace(
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                prompt_tokens_details=SimpleNamespace(cached_tokens=cache_read, cache_write_tokens=cache_write)),
            model=turn.model or model or "claude-agent-sdk",
        )

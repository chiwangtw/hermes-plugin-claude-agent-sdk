"""OpenAI-compatible shim that runs one Hermes Turn through the Claude Agent SDK.

Each ``chat.completions.create()`` call starts a short-lived Claude Code Runtime via
``claude_agent_sdk.query()``, sends the whole Hermes conversation as one prompt, and
returns the minimal OpenAI-client shape Hermes reads (``choices[0].message.content`` /
``.tool_calls``, ``usage``).

Tool contract (see CONTEXT.md):
- Every Hermes Tool is exposed to the Runtime through the Tool Bridge, an in-process MCP
  server named ``hermes`` (so the Runtime sees ``mcp__hermes__<name>``).
- Runtime Built-in Tools are disabled up front (``tools=[]``), so the Runtime can only
  propose Hermes Tools.
- ``permission_mode="dontAsk"`` makes the Runtime deny every Tool Proposal locally; the
  proposal comes back to Hermes as an OpenAI ``tool_calls`` entry and Hermes executes it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.acp_openai_bridge import build_openai_tool_call, completion_to_stream_chunks

logger = logging.getLogger(__name__)

MARKER_BASE_URL = "claude-agent-sdk://local"
MCP_SERVER_NAME = "hermes"
MCP_TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"
DENY_MESSAGE = "Tool execution is unavailable in this environment; the host executes tools."
_DEFAULT_TIMEOUT_SECONDS = 900.0
_DATA_URL_RE = re.compile(r"^data:(?P<media>[\w/+.-]+);base64,(?P<data>.+)$", re.DOTALL)
# Anthropic's sanctioned subscription path: the Runtime must be using the Subscription Login,
# never an API key found in the environment (that would bill pay-per-token).
_ALLOWED_API_KEY_SOURCES = {"none"}

_PROMPT_TRAILER = (
    "Continue the conversation from the latest message above. Historical tool calls and "
    "results are shown for context only; to act, call the provided tools."
)


# ── Message rendering ───────────────────────────────────────────────────────────────────


def _effective_timeout(timeout: Any) -> float:
    """Normalise a float or httpx.Timeout-like object to wall-clock seconds (largest component wins)."""
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


class _TurnResult:
    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.thinking_parts: list[str] = []
        self.tool_calls: list[Any] = []
        self.usage: dict[str, Any] = {}
        self.model: str = ""
        self.stop_reason: str = ""

    @property
    def text(self) -> str:
        return "".join(self.text_parts).strip()

    @property
    def thinking(self) -> str:
        return "".join(self.thinking_parts).strip()


async def _run_turn_async(
    *, model: str | None, system_prompt: str, prompt_blocks: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
    cwd: str, cli_path: str | None,
) -> _TurnResult:
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, StreamEvent, SystemMessage, TextBlock,
        ThinkingBlock, ToolUseBlock)

    bridge = _build_tool_bridge(tools)
    options = ClaudeAgentOptions(
        model=model or None,
        tools=[],  # No Runtime Built-in Tools: only Hermes Tools via the Tool Bridge.
        mcp_servers={MCP_SERVER_NAME: bridge} if bridge else {},
        permission_mode="dontAsk",  # Every Tool Proposal is denied inside the Runtime.
        system_prompt=system_prompt,
        setting_sources=[],  # Ignore ~/.claude settings, CLAUDE.md, hooks.
        strict_mcp_config=True,  # Ignore ~/.claude.json / .mcp.json servers.
        include_partial_messages=True,  # message_start/message_delta/message_stop carry usage + turn end.
        cwd=cwd,
        cli_path=cli_path,
    )

    result = _TurnResult()
    seen_tool_ids: set[str] = set()
    async def _prompt():
        # Streaming-input form: content blocks (text + images) rather than a bare string.
        yield {"type": "user", "message": {"role": "user", "content": prompt_blocks},
               "parent_tool_use_id": None, "session_id": "hermes"}

    async with ClaudeSDKClient(options=options) as client:
        await client.query(_prompt())
        async for message in client.receive_messages():
            if isinstance(message, SystemMessage):
                if message.subtype == "init":
                    source = str(message.data.get("apiKeySource") or "none")
                    if source not in _ALLOWED_API_KEY_SOURCES:
                        raise RuntimeError(
                            f"claude-agent-sdk: the Runtime would authenticate with an API key ({source}), which "
                            "is billed pay-per-token, not against the Claude subscription. Unset ANTHROPIC_API_KEY "
                            "/ ANTHROPIC_AUTH_TOKEN for Hermes, or use the built-in 'anthropic' provider instead.")
                continue
            if isinstance(message, StreamEvent):
                event = message.event or {}
                etype = event.get("type")
                if etype == "message_start":
                    result.usage.update((event.get("message") or {}).get("usage") or {})
                elif etype == "message_delta":
                    result.usage.update(event.get("usage") or {})
                    result.stop_reason = (event.get("delta") or {}).get("stop_reason") or result.stop_reason
                elif etype == "message_stop" and result.tool_calls:
                    # Assistant turn ended on tool_use. Stop here, before the Runtime denies the
                    # proposal locally and spends another API round-trip reacting to that denial.
                    break
                continue
            if isinstance(message, AssistantMessage):
                if message.error:
                    raise RuntimeError(f"claude-agent-sdk: Runtime error '{message.error}'")
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
                    raise RuntimeError(f"claude-agent-sdk: {message.result or message.subtype}")
                break
    return result


def _run_turn(**kwargs: Any) -> _TurnResult:
    """Run the async Turn on a private thread/event loop (Hermes may call us from inside a loop)."""
    import anyio

    outcome: dict[str, Any] = {}

    def _runner() -> None:
        try:
            outcome["value"] = anyio.run(lambda: _run_turn_async(**kwargs))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            outcome["error"] = exc

    timeout_seconds = kwargs.pop("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
    worker = threading.Thread(target=_runner, name="claude-agent-sdk-turn", daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise TimeoutError(f"claude-agent-sdk: Turn exceeded {timeout_seconds:.0f}s.")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


# ── OpenAI-shaped client ────────────────────────────────────────────────────────────────


class ClaudeAgentSDKClient:
    """Minimal OpenAI-client-compatible facade over the Claude Agent SDK."""

    # Declared for agent/auxiliary_client.py: complete client, never re-dispatch through a wire
    # adapter, and safe to call as-is from async code (the Turn runs on its own thread).
    HERMES_SKIP_TRANSPORT_WRAP = True
    HERMES_SKIP_ASYNC_WRAP = True

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, cwd: str | None = None,
                 cli_path: str | None = None, **_: Any):
        self.api_key = api_key or "claude-agent-sdk"  # placeholder: the Runtime owns auth
        self.base_url = base_url or MARKER_BASE_URL
        self._cwd = str(Path(cwd or os.getcwd()).resolve())
        self._cli_path = cli_path or os.getenv("HERMES_CLAUDE_AGENT_SDK_CLI", "").strip() or None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create_chat_completion))
        self.is_closed = False

    def close(self) -> None:
        self.is_closed = True

    def _create_chat_completion(
        self, *, model: str | None = None, messages: list[dict[str, Any]] | None = None, timeout: Any = None,
        tools: list[dict[str, Any]] | None = None, tool_choice: Any = None, stream: bool = False, **_: Any,
    ) -> Any:
        del tool_choice  # The Runtime decides; Hermes' hint is not forwarded yet.
        system_prompt, prompt_blocks = split_messages(messages or [])
        turn = _run_turn(
            model=model, system_prompt=system_prompt, prompt_blocks=prompt_blocks, tools=tools, cwd=self._cwd,
            cli_path=self._cli_path, timeout_seconds=_effective_timeout(timeout))

        usage = turn.usage
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        prompt_tokens = int(usage.get("input_tokens") or 0) + cache_read + cache_write
        completion_tokens = int(usage.get("output_tokens") or 0)

        message = SimpleNamespace(
            content=turn.text or None, tool_calls=turn.tool_calls or None,
            reasoning=turn.thinking or None, reasoning_content=turn.thinking or None, reasoning_details=None,
        )
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="tool_calls" if turn.tool_calls else "stop")],
            usage=SimpleNamespace(
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                prompt_tokens_details=SimpleNamespace(cached_tokens=cache_read, cache_write_tokens=cache_write)),
            model=turn.model or model or "claude-agent-sdk",
        )
        return completion_to_stream_chunks(completion) if stream else completion

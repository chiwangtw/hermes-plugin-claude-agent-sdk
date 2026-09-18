"""Claude Agent SDK provider profile for Hermes Agent.

Routes Hermes LLM turns through the official Claude Agent SDK so a Claude Pro/Max
Subscription Login is billed against plan usage. The SDK-launched Claude Code Runtime
only *proposes* tool calls; Hermes executes them. See docs/HANDOFF.md and CONTEXT.md.

Install: ``git clone <repo> ~/.hermes/plugins/model-providers/claude-agent-sdk``.
"""

from __future__ import annotations

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

from .client import ClaudeAgentSDKClient, MARKER_BASE_URL

# Curated: agentic models that support tool calling. Live listing is not available —
# the Runtime owns auth and exposes no models endpoint.
FALLBACK_MODELS: tuple[str, ...] = (
    "claude-sonnet-5",  # first = suggested default (quota-friendly)
    "claude-fable-5-1",
    "claude-opus-5",
    "claude-haiku-4-5-20251001",
)


# Hermes effort ladder → SDK ``effort`` vocabulary (low/medium/high/xhigh/max). ``none`` is
# kept as-is: the client turns it into "thinking disabled".
HERMES_EFFORT_LADDER: tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
_EFFORT_TO_SDK: dict[str, str] = {
    "none": "none", "minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh",
    "max": "max", "ultra": "max",
}


class ClaudeAgentSDKProfile(ProviderProfile):
    """Claude Agent SDK — external process (bundled Claude Code CLI), Subscription Login."""

    def create_client(self, **client_kwargs: Any) -> Any:
        """Build the SDK client instead of an HTTP client."""
        return ClaudeAgentSDKClient(**client_kwargs)

    def fetch_models(self, **_: Any) -> list[str] | None:
        return list(FALLBACK_MODELS)

    def supported_reasoning_efforts(self, model: str | None) -> tuple[str, ...] | None:
        """Accept the whole Hermes ladder; the mapping below clamps it for the Runtime."""
        return HERMES_EFFORT_LADDER

    def build_api_kwargs_extras(self, *, reasoning_config: dict | None = None, **context: Any) -> tuple[dict, dict]:
        """Hermes never sends ``extra_body["reasoning"]`` to an unknown host, so the effort travels
        as a top-level ``reasoning_effort`` kwarg that :class:`ClaudeAgentSDKClient` understands."""
        if not isinstance(reasoning_config, dict):
            return {}, {}
        if reasoning_config.get("enabled") is False:
            return {}, {"reasoning_effort": "none"}
        effort = str(reasoning_config.get("effort") or "").strip().lower()
        sdk_effort = _EFFORT_TO_SDK.get(effort)
        return {}, ({"reasoning_effort": sdk_effort} if sdk_effort else {})


claude_agent_sdk = ClaudeAgentSDKProfile(
    name="claude-agent-sdk",
    aliases=("claude-sdk", "claude-subscription"),
    display_name="Claude Agent SDK",
    description="Claude Pro/Max subscription via the official Claude Agent SDK",
    signup_url="https://claude.ai/upgrade",
    api_mode="chat_completions",
    env_vars=(),  # The Runtime owns auth (Keychain / ~/.claude); no API key.
    base_url=MARKER_BASE_URL,
    auth_type="external_process",
    # Hermes gates external-process providers on the binary resolving. The SDK bundles its
    # own Claude Code, but a Subscriber logs in through the official ``claude`` CLI anyway.
    process_command="claude",
    process_command_env_vars=("HERMES_CLAUDE_AGENT_SDK_COMMAND", "CLAUDE_CODE_EXECUTABLE"),
    supports_model_listing=False,
    supports_health_check=False,
    # Images in user messages and tool results go straight to the Runtime as image blocks.
    supports_vision=True,
    supports_vision_tool_messages=True,
    fallback_models=FALLBACK_MODELS,
    default_aux_model="claude-haiku-4-5-20251001",
)

register_provider(claude_agent_sdk)

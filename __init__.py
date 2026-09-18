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
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
)


class ClaudeAgentSDKProfile(ProviderProfile):
    """Claude Agent SDK — external process (bundled Claude Code CLI), Subscription Login."""

    def create_client(self, **client_kwargs: Any) -> Any:
        """Build the SDK client instead of an HTTP client."""
        return ClaudeAgentSDKClient(**client_kwargs)

    def fetch_models(self, **_: Any) -> list[str] | None:
        return list(FALLBACK_MODELS)


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
    fallback_models=FALLBACK_MODELS,
    default_aux_model="claude-haiku-4-5-20251001",
)

register_provider(claude_agent_sdk)

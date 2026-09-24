"""A Runtime rejection must reach the Subscriber with the Runtime's reason, and Hermes must not
retry it as if it were an outage (issue #3)."""

from __future__ import annotations

import pytest
from claude_agent_sdk import TextBlock

from agent.error_classifier import classify_api_error

PING = [{"role": "user", "content": "Reply with the single word: pong"}]
# Verbatim from Claude Code 2.1.276 (bundled with claude-agent-sdk 0.2.156) asked for claude-opus-5-5.
RUNTIME_TOO_OLD = ("API Error: 400 Claude Code 2.1.276 does not support this model; version 2.1.280 or newer "
                   "is required. Run 'claude update', or update the Claude desktop app, then try again.")


def _classify(error: Exception, model: str):
    return classify_api_error(error, provider="claude-agent-sdk", model=model)


def test_unknown_model_names_the_problem_and_is_not_retried(client, no_runtime_leak):
    model = "claude-no-such-model-0"
    with pytest.raises(RuntimeError) as caught:
        client.chat.completions.create(model=model, messages=PING, timeout=60)
    error = caught.value
    assert "may not exist" in str(error), "the Runtime's own explanation must survive"
    assert error.status_code == 404
    verdict = _classify(error, model)
    assert verdict.reason.value == "model_not_found" and not verdict.retryable


def _too_old(client_module, *, bundled_runtime=True, model="claude-opus-5-5"):
    return client_module._assistant_error(
        "invalid_request", [TextBlock(text=RUNTIME_TOO_OLD)], bundled_runtime=bundled_runtime, model=model)


def test_runtime_too_old_for_the_model_gives_chat_steps_to_upgrade_the_bundled_runtime(client_module):
    error = _too_old(client_module)
    message = str(error)
    assert "version 2.1.280 or newer is required" in message
    steps = message[message.index("To fix:"):]
    assert steps.index("/model claude-sonnet-5 --provider claude-agent-sdk") < steps.index("```") < steps.index("/new")
    assert "-P claude-agent-sdk claude-agent-sdk" in message and "no -U" in message
    assert "claude update" not in message, "`claude update` does not reach the Runtime bundled in the SDK"
    assert error.status_code == 400
    verdict = _classify(error, "claude-opus-5-5")
    # model_not_found: Hermes leads with "Pick a different model with /model", which is step 1.
    assert verdict.reason.value == "model_not_found" and not verdict.retryable


def test_upgrade_steps_never_park_on_the_model_that_failed(client_module):
    assert "/model claude-sonnet-5 " not in str(_too_old(client_module, model="claude-sonnet-5"))


def test_upgrade_command_survives_the_provider_said_limit_on_a_long_windows_path(client_module, monkeypatch):
    long_exe = "C:\\Users\\" + "a-long-account-name" * 6 + "\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\python.exe"
    monkeypatch.setattr(client_module.sys, "executable", long_exe)
    message = str(_too_old(client_module))
    assert len(message) <= 500, "Hermes shows only the first 500 characters under 'Provider said:'"
    assert f"{long_exe} -P claude-agent-sdk claude-agent-sdk" in message
    assert message.rstrip().endswith("3. /new")


def test_runtime_too_old_keeps_its_own_advice_for_a_standalone_claude(client_module):
    # HERMES_CLAUDE_AGENT_SDK_CLI points at the Subscriber's own `claude`: `claude update` is the fix.
    error = _too_old(client_module, bundled_runtime=False)
    assert "claude update" in str(error)
    assert "-P claude-agent-sdk" not in str(error)

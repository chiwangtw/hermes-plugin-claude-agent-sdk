"""Compliant means the Runtime bills the Subscriber's own plan — never an API key."""

from __future__ import annotations

import pytest

from conftest import TEST_MODEL

PING = [{"role": "user", "content": "Reply with the single word: pong"}]


def test_refuses_to_run_on_an_api_key(client, no_runtime_leak, monkeypatch):
    # The Runtime inherits the host environment; a key here would silently switch billing.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-not-a-real-key")
    with pytest.raises(RuntimeError, match="API key"):
        client.chat.completions.create(model=TEST_MODEL, messages=PING, timeout=60)

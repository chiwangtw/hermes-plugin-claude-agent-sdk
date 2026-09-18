"""Hermes' reasoning effort must reach the Runtime as the SDK's ``effort`` / thinking switch."""

from __future__ import annotations

import threading
import time

from conftest import TEST_MODEL, runtime_processes

SHORT = [{"role": "user", "content": "Reply with the single word: pong"}]


def _runtime_cmdline_during_turn(client, **create_kwargs) -> str:
    """Run one Turn on a thread and capture the Runtime's command line while it is alive."""
    seen: dict = {}

    def _turn():
        try:
            seen["result"] = client.chat.completions.create(model=TEST_MODEL, messages=SHORT, timeout=60, **create_kwargs)
        except BaseException as exc:  # noqa: BLE001
            seen["error"] = exc

    worker = threading.Thread(target=_turn, daemon=True)
    worker.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and "cmd" not in seen:
        procs = runtime_processes()
        if procs:
            seen["cmd"] = procs[0]
        time.sleep(0.05)
    worker.join(60)
    assert "error" not in seen, seen.get("error")
    assert "cmd" in seen, "Runtime process was never observed"
    return seen["cmd"]


def test_high_effort_reaches_runtime(client, no_runtime_leak):
    cmd = _runtime_cmdline_during_turn(client, reasoning_effort="high")
    assert "--effort high" in cmd
    assert "--thinking disabled" not in cmd


def test_none_effort_disables_thinking(client, no_runtime_leak):
    cmd = _runtime_cmdline_during_turn(client, reasoning_effort="none")
    assert "--thinking disabled" in cmd
    assert "--effort" not in cmd

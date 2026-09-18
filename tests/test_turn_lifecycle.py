"""A Turn that ends early (timeout, host shutdown) must not leave a Runtime process behind."""

from __future__ import annotations

import time

import pytest

from conftest import TEST_MODEL, wait_for_runtime

LONG_TASK = [
    {"role": "system", "content": "You are a verbose writer."},
    {"role": "user", "content": "Write a 3000-word essay about the history of the Roman aqueducts. Do not stop early."},
]


def test_timeout_raises_and_terminates_runtime(client, no_runtime_leak):
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        client.chat.completions.create(model=TEST_MODEL, messages=LONG_TASK, timeout=3)
    assert time.monotonic() - started < 10, "timeout was not enforced promptly"


def test_close_during_turn_stops_it_and_terminates_runtime(client, no_runtime_leak):
    import threading

    outcome: dict = {}

    def _turn():
        try:
            outcome["value"] = client.chat.completions.create(model=TEST_MODEL, messages=LONG_TASK, timeout=60)
        except BaseException as exc:  # noqa: BLE001
            outcome["error"] = exc

    worker = threading.Thread(target=_turn, daemon=True)
    worker.start()
    wait_for_runtime()
    started = time.monotonic()
    client.close()
    worker.join(10)
    assert not worker.is_alive(), "create() did not return after close()"
    assert time.monotonic() - started < 10
    assert "error" in outcome, "create() should fail once the client is closed"


def test_client_is_reusable_after_close(client, no_runtime_leak):
    client.close()
    reply = client.chat.completions.create(
        model=TEST_MODEL, messages=[{"role": "user", "content": "Reply with the single word: pong"}], timeout=60)
    assert "pong" in (reply.choices[0].message.content or "").lower()

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


PING = [{"role": "user", "content": "Reply with the single word: pong"}]


def test_create_called_from_inside_a_running_loop_without_await(client, no_runtime_leak):
    """Hermes' sync callers may run on a thread that already has a loop; ``create`` must still hand
    back a usable completion (and ``StreamChunks`` under ``stream=True``) when not awaited."""
    import asyncio

    async def _sync_calls():
        reply = client.chat.completions.create(model=TEST_MODEL, messages=PING, timeout=60)
        assert "pong" in (reply.choices[0].message.content or "").lower()
        chunks = client.chat.completions.create(model=TEST_MODEL, messages=PING, timeout=60, stream=True)
        assert not hasattr(chunks, "choices")  # Hermes' probe for "complete response despite stream=True"
        deltas = [chunk.choices[0].delta for chunk in chunks if chunk.choices]
        assert "pong" in "".join(d.content or "" for d in deltas).lower()
        # The same call awaited yields the complete response (Hermes' async path accepts it).
        awaited = await client.chat.completions.create(model=TEST_MODEL, messages=PING, timeout=60, stream=True)
        assert "pong" in (awaited.choices[0].message.content or "").lower()

    asyncio.run(_sync_calls())


def test_awaited_timeout_raises_and_terminates_runtime(client, no_runtime_leak):
    import asyncio

    async def _ask():
        return await client.chat.completions.create(model=TEST_MODEL, messages=LONG_TASK, timeout=3)

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(_ask())
    assert time.monotonic() - started < 10, "timeout was not enforced promptly"


def test_cancelling_the_awaiting_task_terminates_runtime(client, no_runtime_leak):
    """Hermes abandons async aux tasks on disconnect/shutdown; the Turn and its Runtime must go too."""
    import asyncio

    async def _aux_task():  # shaped like Hermes' async_call_llm: a coroutine that awaits create()
        return await client.chat.completions.create(model=TEST_MODEL, messages=LONG_TASK, timeout=60)

    async def _ask():
        task = asyncio.create_task(_aux_task())
        await asyncio.to_thread(wait_for_runtime)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    started = time.monotonic()
    asyncio.run(_ask())
    assert time.monotonic() - started < 15

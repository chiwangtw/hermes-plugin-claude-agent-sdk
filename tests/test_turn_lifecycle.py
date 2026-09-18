"""A Turn that ends early (timeout, host shutdown) must not leave a Runtime process behind."""

from __future__ import annotations

import time

import pytest

from conftest import TEST_MODEL

LONG_TASK = [
    {"role": "system", "content": "You are a verbose writer."},
    {"role": "user", "content": "Write a 3000-word essay about the history of the Roman aqueducts. Do not stop early."},
]


def test_timeout_raises_and_terminates_runtime(client, no_runtime_leak):
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        client.chat.completions.create(model=TEST_MODEL, messages=LONG_TASK, timeout=3)
    assert time.monotonic() - started < 10, "timeout was not enforced promptly"

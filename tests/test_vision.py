"""Images reach the Runtime whether they arrive in a user message or inside a tool result."""

from __future__ import annotations

import base64
import struct
import zlib

from conftest import TEST_MODEL


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A dependency-free solid-colour PNG (8-bit RGB)."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


RED_DATA_URL = "data:image/png;base64," + base64.b64encode(_solid_png(64, 64, (255, 0, 0))).decode()
QUESTION = "What colour is the image? Answer with one lowercase word only."


def test_image_inside_tool_result_is_seen(client, no_runtime_leak):
    messages = [
        {"role": "system", "content": "You are a terse assistant."},
        {"role": "user", "content": "Take a screenshot with your tool, then tell me its colour."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_shot", "type": "function", "function": {"name": "browser_snapshot", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_shot", "content": [
            {"type": "text", "text": "Screenshot captured."},
            {"type": "image_url", "image_url": {"url": RED_DATA_URL}}]},
        {"role": "user", "content": QUESTION},
    ]
    reply = client.chat.completions.create(model=TEST_MODEL, messages=messages, timeout=60)
    assert "red" in (reply.choices[0].message.content or "").lower()


def test_image_in_user_message_is_seen(client, no_runtime_leak):
    messages = [{"role": "user", "content": [
        {"type": "text", "text": QUESTION},
        {"type": "image_url", "image_url": {"url": RED_DATA_URL}}]}]
    reply = client.chat.completions.create(model=TEST_MODEL, messages=messages, timeout=60)
    assert "red" in (reply.choices[0].message.content or "").lower()

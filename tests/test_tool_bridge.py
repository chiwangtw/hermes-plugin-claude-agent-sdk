"""Every Hermes Tool reaches the Runtime through the Tool Bridge; the Runtime only proposes."""

from __future__ import annotations

import json
import threading

from conftest import TEST_MODEL, wait_for_runtime

READ_FILE = {"type": "function", "function": {
    "name": "read_file", "description": "Read a file from disk.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}


def test_runtime_proposes_hermes_tool_and_built_ins_are_off(client, no_runtime_leak):
    seen: dict = {}

    def _turn():
        seen["reply"] = client.chat.completions.create(
            model=TEST_MODEL, tools=[READ_FILE], timeout=60,
            messages=[{"role": "user", "content": "Use your read_file tool on /etc/hosts. Call the tool now."}])

    worker = threading.Thread(target=_turn, daemon=True)
    worker.start()
    cmd = wait_for_runtime()
    worker.join(60)

    assert "--tools  " in cmd or cmd.endswith("--tools "), "Built-in Tools must be disabled"
    choice = seen["reply"].choices[0]
    assert choice.finish_reason == "tool_calls"
    [call] = choice.message.tool_calls
    assert call.function.name == "read_file", "Tool Bridge prefix must be stripped on the way back"
    assert json.loads(call.function.arguments)["path"] == "/etc/hosts"


def test_tool_result_round_trip_answers(client, no_runtime_leak):
    messages = [
        {"role": "user", "content": "How many lines are in the file? Use read_file."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"/x\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "alpha\nbeta\ngamma\n"},
        {"role": "user", "content": "Answer with the number only."},
    ]
    reply = client.chat.completions.create(model=TEST_MODEL, messages=messages, tools=[READ_FILE], timeout=60)
    assert reply.choices[0].finish_reason == "stop"
    assert "3" in (reply.choices[0].message.content or "")

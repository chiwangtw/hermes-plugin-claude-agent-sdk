"""Live test harness: every test here talks to the real Claude Agent SDK on the developer's own
Subscription Login (no fakes, by decision — see docs/PRD.md). Run with Hermes' venv python so
``claude_agent_sdk`` and Hermes' own modules resolve:

    $HERMES_HOME/hermes-agent/venv/bin/python -m pytest tests -v
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
HERMES_ROOT = Path(os.environ.get("HERMES_AGENT_ROOT") or HERMES_HOME / "hermes-agent")
TEST_MODEL = "claude-haiku-4-5-20251001"

if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))


def _load_client_module():
    name = "hermes_claude_agent_sdk_client"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PLUGIN_ROOT / "client.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.fixture(scope="session")
def client_module():
    return _load_client_module()


@pytest.fixture
def client(client_module, tmp_path):
    return client_module.ClaudeAgentSDKClient(cwd=str(tmp_path))


def runtime_processes() -> list[str]:
    """Command lines of live Runtime processes descended from this test process. Matching on the
    SDK's argv is test-only: the SDK gives us no handle on the process it spawns."""
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    children: dict[int, list[tuple[int, str]]] = {}
    for pid, ppid, cmd in rows:
        children.setdefault(ppid, []).append((pid, cmd))
    found: list[str] = []
    stack = [os.getpid()]
    while stack:
        for pid, cmd in children.get(stack.pop(), []):
            stack.append(pid)
            if "claude" in cmd and "--output-format" in cmd:
                found.append(cmd)
    return found


def wait_for_runtime(timeout: float = 15.0) -> str:
    """Block until a Runtime process is observed; returns its command line."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if procs := runtime_processes():
            return procs[0]
        time.sleep(0.05)
    raise AssertionError("Runtime process was never observed")


@pytest.fixture
def no_runtime_leak():
    """Assert no Runtime process outlives the test."""
    assert runtime_processes() == [], "Runtime processes already running before the test"
    yield
    import time
    deadline = time.time() + 5
    while runtime_processes() and time.time() < deadline:
        time.sleep(0.2)
    assert runtime_processes() == [], "Runtime process leaked after the test"


@pytest.fixture(scope="session")
def profile():
    """The registered ProviderProfile, loaded the way Hermes loads a user plugin directory."""
    name = "_hermes_user_provider_claude_agent_sdk_test"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, PLUGIN_ROOT / "__init__.py", submodule_search_locations=[str(PLUGIN_ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].claude_agent_sdk

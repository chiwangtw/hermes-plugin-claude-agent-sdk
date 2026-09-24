"""The automatic upgrade of the bundled Runtime (issue #5), run for real: uv resolves from PyPI
(or its cache) into throwaway virtualenvs. Hermes' own environment is never the target."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

import pytest

from conftest import load_plugin_module

STALE = "0.2.156"  # bundles Claude Code 2.1.276, too old for claude-opus-5-5
UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(UV is None, reason="the upgrade runs uv")


@pytest.fixture(scope="module")
def upgrade_module():
    return sys.modules[load_plugin_module().__name__ + ".runtime_upgrade"]


def _venv(tmp_path, *requirements: str) -> str:
    subprocess.run([UV, "venv", "-q", "-p", "3.11", str(tmp_path / "venv")], check=True)
    python = tmp_path / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([UV, "pip", "install", "-q", "--python", str(python), *requirements], check=True)
    return str(python)


def _installed(python: str) -> dict[str, str]:
    out = subprocess.run([UV, "pip", "freeze", "--python", python], capture_output=True, text=True, check=True)
    return dict(line.split("==", 1) for line in out.stdout.splitlines() if "==" in line)


def test_upgrades_the_sdk_and_nothing_else(upgrade_module, tmp_path):
    python = _venv(tmp_path, f"claude-agent-sdk=={STALE}")
    before = _installed(python)
    outcome = upgrade_module.upgrade(STALE, python=python)
    assert outcome.upgraded, outcome.detail
    after = _installed(python)
    assert after["claude-agent-sdk"] == outcome.detail != STALE
    assert {name for name, version in before.items() if after.get(name) != version} == {"claude-agent-sdk"}


def test_leaves_the_environment_alone_when_an_installed_package_would_move(upgrade_module, tmp_path):
    # claude-agent-sdk 0.1.30 accepts mcp 1.22; current releases need mcp >= 1.23, which Hermes may pin lower.
    python = _venv(tmp_path, "claude-agent-sdk==0.1.30", "mcp==1.22.0")
    before = _installed(python)
    outcome = upgrade_module.upgrade("0.1.30", python=python)
    assert not outcome.upgraded and "mcp" in outcome.blocked_by
    assert _installed(python) == before


def test_waits_for_running_runtimes_and_gives_up_in_time(upgrade_module, tmp_path):
    python = _venv(tmp_path, f"claude-agent-sdk=={STALE}")
    before = _installed(python)
    holding, release = threading.Event(), threading.Event()

    def turn():
        with upgrade_module.runtime_slot():  # a Runtime still running elsewhere in the process
            holding.set()
            release.wait(30)

    worker = threading.Thread(target=turn, daemon=True)
    worker.start()
    assert holding.wait(5)
    try:
        outcome = upgrade_module.upgrade(STALE, python=python, idle_wait=0.5)
    finally:
        release.set()
        worker.join(5)
    assert not outcome.upgraded and "Runtime running" in outcome.detail
    assert _installed(python) == before
    # A busy Runtime is transient, not a failed attempt: once it is gone, the upgrade goes ahead.
    assert upgrade_module.upgrade(STALE, python=python).upgraded


def test_a_turn_that_lost_the_race_just_retries(upgrade_module, tmp_path):
    # Two Turns hit the same too-old Runtime; the first upgraded while the second waited.
    python = _venv(tmp_path, "claude-agent-sdk")
    outcome = upgrade_module.upgrade(STALE, python=python)
    assert outcome.upgraded and outcome.detail == _installed(python)["claude-agent-sdk"]


def test_switch_off_touches_nothing(upgrade_module, tmp_path, monkeypatch):
    monkeypatch.setenv(upgrade_module.SWITCH_ENV, "0")
    outcome = upgrade_module.upgrade(STALE, python=str(tmp_path / "no-such-python"))
    assert not outcome.upgraded and upgrade_module.SWITCH_ENV in outcome.detail


def test_reads_uv_install_output(upgrade_module):
    # Verbatim uv 0.12 `pip install --dry-run` stderr.
    output = ("Resolved 30 packages in 492ms\nWould install 3 packages\n - anyio==4.12.1\n + anyio==4.15.1\n"
              " - claude-agent-sdk==0.2.156\n + claude-agent-sdk==0.2.159\n + jsonschema==4.25.1\n")
    assert upgrade_module.planned_changes(output) == {
        "anyio": ("4.12.1", "4.15.1"), "claude-agent-sdk": ("0.2.156", "0.2.159"), "jsonschema": (None, "4.25.1")}


def test_a_project_uv_config_in_the_working_directory_does_not_hide_the_new_sdk(upgrade_module, tmp_path,
                                                                                monkeypatch):
    # Hermes' own checkout sets `exclude-newer = "14 days"`; a gateway started there saw "no changes".
    python = _venv(tmp_path, f"claude-agent-sdk=={STALE}")
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\n\n[tool.uv]\nexclude-newer = "2026-09-18T12:00:00Z"\n')
    monkeypatch.chdir(project)
    outcome = upgrade_module.upgrade(STALE, python=python)
    assert outcome.upgraded, outcome.detail

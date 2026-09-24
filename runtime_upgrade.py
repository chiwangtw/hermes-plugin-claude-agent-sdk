"""Guarded upgrade of the Runtime bundled in claude-agent-sdk (issue #5).

A model released after the installed SDK is refused by the API ("Claude Code X does not support
this model; version Y or newer is required"). The fix is upgrading the SDK in Hermes' own
environment, which a Subscriber on a chat app cannot do reliably by hand: the command gets
mangled on the way, and a model asked to run it may "fix" it into ``-U``, which also moves
packages Hermes pins. The plugin therefore runs the exact command itself, but only when:

- the switch is on (``HERMES_CLAUDE_AGENT_SDK_AUTO_UPGRADE``, default on);
- a dry run shows that ``claude-agent-sdk`` is the only installed package that would change
  (new dependencies may be added: nothing Hermes pins moves);
- no Runtime is running: the upgrade replaces the bundled binary, which Windows keeps locked
  while it runs;
- this installed version has not already been tried in this process.

Anything else leaves the environment untouched, and the caller shows the manual steps together
with the reason.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PACKAGE = "claude-agent-sdk"
SWITCH_ENV = "HERMES_CLAUDE_AGENT_SDK_AUTO_UPGRADE"
_OFF_VALUES = frozenset({"0", "false", "no", "off"})
_DRY_RUN_TIMEOUT_SECONDS = 120.0
_INSTALL_TIMEOUT_SECONDS = 600.0  # the wheel carries a ~100 MB Runtime binary
_IDLE_WAIT_SECONDS = 60.0
# uv reports planned and applied changes as " - name==version" / " + name==version" on stderr.
_CHANGE_RE = re.compile(r"^ ([+-]) ([A-Za-z0-9._-]+)==(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Outcome:
    """``upgraded`` → ``detail`` is the new version; otherwise it says, briefly, why not (it ends up
    inside the error Hermes shows, which is capped at 500 characters)."""

    upgraded: bool
    detail: str
    # Installed packages besides the SDK a dry run wanted to change: the fix is updating Hermes.
    blocked_by: tuple[str, ...] = ()


def enabled() -> bool:
    return os.getenv(SWITCH_ENV, "").strip().lower() not in _OFF_VALUES


def planned_changes(uv_output: str) -> dict[str, tuple[str | None, str | None]]:
    """``{package: (old version, new version)}`` from uv's install output; ``None`` = absent."""
    changes: dict[str, list[str | None]] = {}
    for sign, name, version in _CHANGE_RE.findall(uv_output):
        entry = changes.setdefault(name.lower().replace("_", "-"), [None, None])
        entry[0 if sign == "-" else 1] = version
    return {name: (old, new) for name, (old, new) in changes.items()}


# ── Runtime gate ────────────────────────────────────────────────────────────────────────
# Turns hold a slot while their Runtime process lives; an upgrade waits for zero slots and blocks
# new ones until it is done.

_gate = threading.Condition()
_running_runtimes = 0
_upgrading = False


@contextlib.contextmanager
def runtime_slot():
    """Held for the lifetime of one Runtime process."""
    global _running_runtimes
    with _gate:
        _gate.wait_for(lambda: not _upgrading)
        _running_runtimes += 1
    try:
        yield
    finally:
        with _gate:
            _running_runtimes -= 1
            _gate.notify_all()


@contextlib.contextmanager
def _exclusive(timeout: float):
    """Yields True once no Runtime is running (new ones wait), False if that did not happen in time."""
    global _upgrading
    with _gate:
        _upgrading = True
        idle = _gate.wait_for(lambda: _running_runtimes == 0, timeout)
        if not idle:
            _upgrading = False
            _gate.notify_all()
    try:
        yield idle
    finally:
        if idle:
            with _gate:
                _upgrading = False
                _gate.notify_all()


# ── Upgrade ─────────────────────────────────────────────────────────────────────────────

_upgrade_lock = threading.Lock()
_attempted_from: set[tuple[str, str]] = set()  # (interpreter, version) already tried this process


def _find_uv() -> str | None:
    if found := shutil.which("uv"):
        return found
    home = Path(os.getenv("HERMES_HOME") or Path.home() / ".hermes")
    for candidate in (home / "bin" / "uv.exe", home / "bin" / "uv"):
        if candidate.is_file():
            return str(candidate)
    return None


def _uv(uv: str, *args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    # Run outside any project: uv reads the pyproject.toml / uv.toml around its working directory, and
    # Hermes' own checkout sets `exclude-newer = "14 days"`, which hides exactly the fresh SDK a new
    # model needs. The Subscriber's own uv settings (user config, UV_* variables) still apply.
    return subprocess.run([uv, *args], capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, check=False, cwd=tempfile.gettempdir())


def _last_line(result: subprocess.CompletedProcess[str]) -> str:
    lines = [line.strip() for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
    return (lines[-1] if lines else f"exit {result.returncode}")[:120]


def installed_version(python: str = sys.executable) -> str | None:
    uv = _find_uv()
    if uv is None:
        return None
    result = _uv(uv, "pip", "show", "--python", python, PACKAGE, timeout=_DRY_RUN_TIMEOUT_SECONDS)
    match = re.search(r"^Version:\s*(\S+)", result.stdout or "", re.MULTILINE)
    return match.group(1) if match else None


def upgrade(from_version: str | None, *, python: str = sys.executable,
            idle_wait: float = _IDLE_WAIT_SECONDS) -> Outcome:
    """Upgrade ``claude-agent-sdk`` in ``python``'s environment, if the guardrails allow it.
    ``from_version`` is what was installed when the Turn failed: if it has changed since, another
    Turn already upgraded and there is nothing to do but retry."""
    if not enabled():
        return Outcome(False, f"off ({SWITCH_ENV}=0)")
    uv = _find_uv()
    if uv is None:
        return Outcome(False, "uv not found")
    with _upgrade_lock:
        current = installed_version(python)
        if current and from_version and current != from_version:
            return Outcome(True, current)
        if (python, current or "") in _attempted_from:
            return Outcome(False, f"already tried from {current}")

        install = ("pip", "install", "--python", python, "-P", PACKAGE, PACKAGE)
        try:
            plan = _uv(uv, *install, "--dry-run", timeout=_DRY_RUN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return Outcome(False, "dry run timed out")
        if plan.returncode != 0:
            return Outcome(False, f"dry run failed: {_last_line(plan)}")
        changes = planned_changes(plan.stderr + plan.stdout)
        if PACKAGE not in changes:
            return Outcome(False, f"no newer {PACKAGE} than {current}")
        moved = tuple(sorted(n for n, (old, _) in changes.items() if n != PACKAGE and old is not None))
        if moved:
            return Outcome(False, "would also change " + ", ".join(moved), blocked_by=moved)
        target = changes[PACKAGE][1]

        with _exclusive(idle_wait) as idle:
            if not idle:
                return Outcome(False, "another Turn kept its Runtime running")
            logger.info("claude-agent-sdk: upgrading %s %s -> %s for %s", PACKAGE, current, target, python)
            _attempted_from.add((python, current or ""))  # once per version: a failure is not retried per Turn
            try:
                applied = _uv(uv, *install, timeout=_INSTALL_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                return Outcome(False, "upgrade timed out")
        if applied.returncode != 0:
            logger.warning("claude-agent-sdk: upgrade failed: %s", applied.stderr)
            return Outcome(False, f"upgrade failed: {_last_line(applied)}")
        now = installed_version(python)
        if now != target:
            return Outcome(False, f"upgrade left {PACKAGE} at {now}")
        logger.info("claude-agent-sdk: upgraded %s to %s", PACKAGE, now)
        return Outcome(True, now)

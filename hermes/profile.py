"""The profile-level half of the Hermes plugin: files, and calls to the CLI.

Nothing in this file derives anything. The seed, the phenotype and the text are
`exoself born`, `exoself show` and `exoself develop`; the managed block is
`exoself.install.upsert_block`, the same function `exoself install` and
`exoself develop` use, so a block written here is refreshed by them and vice
versa.

Layout, inside the Hermes profile's home:

    .exoself/birth.json   the one draw; never overwritten, no reroll path
    .exoself/state.json   the history; created by the first `develop`
    SOUL.md               the rendered temperament in a managed block
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

CERT_REL = Path(".exoself") / "birth.json"
STATE_REL = Path(".exoself") / "state.json"
SOUL_NAME = "SOUL.md"
TIMEOUT = 30
# Hermes abandons a plugin whose register() overruns plugins.load_timeout_seconds
# (10s by default), so the load path gives up well before that instead of being
# killed mid-write.
LOAD_TIMEOUT = 8


class ExoselfError(RuntimeError):
    """A CLI call failed; the message is the CLI's own, so it stays truthful."""


def hermes_home() -> Path:
    """The active profile's home, resolved per call.

    One Hermes process can serve several profiles, so this is never cached: the
    hook that is running has already bound the profile it belongs to.
    """
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()).expanduser()


def paths(home: Path) -> tuple[Path, Path, Path]:
    return home / CERT_REL, home / STATE_REL, home / SOUL_NAME


def cli() -> list[str]:
    """How to run this implementation: the console script, else the module."""
    exe = shutil.which("exoself")
    return [exe] if exe else [sys.executable, "-m", "exoself.cli"]


def _run(args: list[str], *, timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        cli() + args, capture_output=True, text=True, timeout=timeout, check=False,
    )


def _failure(proc: subprocess.CompletedProcess, what: str) -> ExoselfError:
    detail = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    return ExoselfError(f"{what} failed" + (f": {detail}" if detail else ""))


def _atomic_write(path: Path, text: str) -> None:
    """SOUL.md is the identity file; a half-written one is worse than an old one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".soul-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)          # mkstemp's 0600 is not the user's choice
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise


def render_soul(home: Path, desire: bool = False, timeout: int = TIMEOUT) -> str:
    """`exoself show --format soul`, with the state when there is one."""
    cert, state, _ = paths(home)
    args = ["show", str(cert)]
    if state.exists():
        args += ["--state", str(state)]
    if desire:
        args += ["--desire"]
    args += ["--format", "soul"]
    proc = _run(args, timeout=timeout)
    if proc.returncode != 0:
        raise _failure(proc, "exoself show")
    return proc.stdout


def write_block(home: Path, desire: bool = False, timeout: int = TIMEOUT) -> bool:
    """Put the rendered temperament in SOUL.md's managed block, keeping the rest.

    Returns whether the file changed. `upsert_block` is idempotent: one block,
    replaced in place, never a second one, and the user's own text is left
    exactly where it was.
    """
    from exoself import install

    _, _, soul_path = paths(home)
    existing = ""
    if soul_path.exists():
        existing = soul_path.read_text(encoding="utf-8")
    updated = install.upsert_block(existing, render_soul(home, desire, timeout))
    if updated == existing:
        return False
    _atomic_write(soul_path, updated)
    return True


def ensure(home: Path, desire: bool = False, timeout: int = TIMEOUT) -> dict:
    """Draw once, then keep the block current. Safe to call on every load.

    `born` writes with mode "x" and refuses an existing certificate, so two
    loads racing each other still produce one agent: the loser sees the file
    and uses it. There is deliberately no path here that redraws.
    """
    cert, _, _ = paths(home)
    drawn = False
    if not cert.exists():
        cert.parent.mkdir(parents=True, exist_ok=True)
        proc = _run(["born", "-o", str(cert)], timeout=timeout)
        if proc.returncode != 0:
            if not cert.exists():          # a real failure, not a lost race
                raise _failure(proc, "exoself born")
        else:
            drawn = True
    return {"drawn": drawn, "soul": write_block(home, desire, timeout), "certificate": str(cert)}


def develop(home: Path, event: str) -> str:
    """One experience event. Creates the state file on the first call."""
    cert, state, _ = paths(home)
    proc = _run(["develop", str(cert), str(state), "--event", event])
    if proc.returncode != 0:
        raise _failure(proc, "exoself develop")
    return (proc.stdout or "").strip()


def status(home: Path) -> str:
    """Who this agent is. Read-only: never draws, never develops."""
    cert, state, _ = paths(home)
    if not cert.exists():
        return ("This profile has no certificate yet. The plugin draws one when it loads; "
                "if this persists, exoself is not installed where Hermes can run it.")
    args = ["status", str(cert)]
    if state.exists():
        args += ["--state", str(state)]
    proc = _run(args)
    if proc.returncode != 0:
        raise _failure(proc, "exoself status")
    return (proc.stdout or "").strip()


def verify(home: Path) -> tuple[bool, str]:
    """Recompute the certificate against its registry. Read-only."""
    cert, _, _ = paths(home)
    if not cert.exists():
        return False, "No certificate yet, so there is nothing to verify."
    proc = _run(["verify", str(cert)])
    return proc.returncode == 0, ((proc.stdout or "") + (proc.stderr or "")).strip()

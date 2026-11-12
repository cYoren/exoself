"""The profile-level half of the Hermes plugin: files, and calls into exoself.

Nothing in this file derives anything. The seed, the phenotype and the text are
`exoself.born`, `exoself.render_soul` and `exoself.develop` (see
:mod:`.engine`), and the managed block is
`exoself.install.upsert_block`, the same function `exoself install` and
`exoself develop` use, so a block written here is refreshed by them and vice
versa.

Everything is called **in this process**. The plugin must never spawn a child:
a child would inherit the host's environment (every provider key included),
put the turn's words on a command line where `ps` can read them, and race the
plugin loader's own timeout.

Layout, inside the Hermes profile's home:

    .exoself/birth.json   the one draw; never overwritten, no reroll path
    .exoself/state.json   the history; created by the first `develop`
    SOUL.md               the rendered temperament in a managed block
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path

from . import engine

CERT_REL = Path(".exoself") / "birth.json"
STATE_REL = Path(".exoself") / "state.json"
SOUL_NAME = "SOUL.md"

ExoselfError = engine.ExoselfError


def hermes_home() -> Path:
    """The active profile's home, resolved per call.

    One Hermes process can serve several profiles, so this is never cached: the
    hook that is running has already bound the profile it belongs to.
    """
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()).expanduser()


def paths(home: Path) -> tuple[Path, Path, Path]:
    return home / CERT_REL, home / STATE_REL, home / SOUL_NAME


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


def render_soul(home: Path, desire: bool = False) -> str:
    """The rendered temperament, with the state when there is one."""
    cert, state, _ = paths(home)
    return engine.render_soul(cert, state if state.exists() else None, desire)


def write_block(home: Path, desire: bool = False) -> bool:
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
    updated = install.upsert_block(existing, render_soul(home, desire))
    if updated == existing:
        return False
    _atomic_write(soul_path, updated)
    return True


def ensure(home: Path, desire: bool = False) -> dict:
    """Draw once, then keep the block current. Safe to call on every load.

    `born` writes with mode "x" and refuses an existing certificate, so two
    loads racing each other still produce one agent: the loser sees the file
    and uses it. There is deliberately no path here that redraws.
    """
    cert, _, _ = paths(home)
    drawn = False
    if not cert.exists():
        cert.parent.mkdir(parents=True, exist_ok=True)
        try:
            engine.born(cert)
            drawn = True
        except engine.ExoselfError:
            if not cert.exists():          # a real failure, not a lost race
                raise
    return {"drawn": drawn, "soul": write_block(home, desire), "certificate": str(cert)}


def develop(home: Path, event: str) -> str:
    """One experience event. Creates the state file on the first call."""
    cert, state, _ = paths(home)
    new_state = engine.develop(cert, state, event)
    drift = ", ".join(f"{k}:{v:+d}" for k, v in new_state["drift"].items())
    tension = ", ".join(f"{k}:{v}" for k, v in new_state["tension"].items())
    return f"event {new_state['events']} applied, drift [{drift}], tension [{tension}]"


def status(home: Path) -> str:
    """Who this agent is. Read-only: never draws, never develops."""
    cert, state, _ = paths(home)
    if not cert.exists():
        return ("This profile has no certificate yet. The plugin draws one when it loads; "
                "if this persists, exoself is not installed where Hermes can run it.")
    return engine.status(cert, state if state.exists() else None)


def verify(home: Path) -> tuple[bool, str]:
    """Recompute the certificate against its registry. Read-only."""
    cert, _, _ = paths(home)
    if not cert.exists():
        return False, "No certificate yet, so there is nothing to verify."
    return engine.verify(cert)

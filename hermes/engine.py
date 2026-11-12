"""In-process entry points for the plugin.

The CLI is for people and shell scripts. The plugin must not spawn it: the
child would inherit Hermes' environment (every provider key included), put its
arguments where any local user can read them with ``ps``, and race the plugin
loader's own timeout. These functions do what ``born``, ``show``, ``develop``,
``status`` and ``verify`` do, in this process.

They live in the plugin rather than in the ``exoself`` package because the
package the catalog installs is 0.3.0, and adding a module to it would need a
release before this plugin could load at all. Nothing here derives anything:
every derivation stays in :mod:`exoself.core`, and the block is still written by
``exoself.install``.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from exoself import core, render

LOCK_WAIT = 10.0
LOCK_STALE = 60.0


class ExoselfError(RuntimeError):
    """A derivation step failed; the message is safe to show the operator."""


def _read(path: str | os.PathLike) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ExoselfError(f"{path}: no such file") from None
    except json.JSONDecodeError as e:
        raise ExoselfError(f"{path}: {e.msg} at line {e.lineno} column {e.colno}") from None
    except OSError as e:
        raise ExoselfError(f"{path}: {e.strerror}") from None


def _write_new(path: str | os.PathLike, data: dict) -> None:
    """Write a birth certificate with mode "x": an agent is born once."""
    try:
        with open(path, "x", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    except FileExistsError:
        raise ExoselfError(f"{path} already exists") from None
    except OSError as e:
        raise ExoselfError(f"{path}: {e.strerror}") from None


def _write_atomic(path: str | os.PathLike, data: dict) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise ExoselfError(f"{path}: {e.strerror}") from None


@contextlib.contextmanager
def state_lock(state_path: str | os.PathLike):
    """Hold a sibling lock file for a read-modify-write of the state.

    Two hosts can record sessions for the same agent, and development is
    read-modify-write on a chained file, so without this the later writer
    silently erases the earlier event. The Rust side takes the same lock.
    """
    lock = f"{state_path}.lock"
    started = time.monotonic()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - os.path.getmtime(lock) > LOCK_STALE:
                    os.unlink(lock)
                    continue
            if time.monotonic() - started > LOCK_WAIT:
                raise ExoselfError(f"{lock}: another develop is still running") from None
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(OSError):
            os.unlink(lock)


def _registry_for(cert: dict) -> tuple[dict, str]:
    try:
        return core.load_registry(name=cert.get("registry", ""))
    except ValueError as e:
        raise ExoselfError(str(e)) from None


def born(cert_path: str | os.PathLike) -> dict:
    """Draw one seed and write the certificate. Refuses to overwrite anything."""
    if os.path.exists(cert_path):
        raise ExoselfError(
            f"{cert_path} already exists. An agent is born once; refusing to reroll."
        )
    reg, reg_hash = core.load_registry()
    cert = core.born(reg_hash, f"{reg['registry']}@{reg['version']}")
    _write_new(cert_path, cert)
    return cert


def render_soul(
    cert_path: str | os.PathLike,
    state_path: str | os.PathLike | None = None,
    desire: bool = False,
) -> str:
    """The temperament rendered in soul format, with the state when there is one."""
    cert = _read(cert_path)
    reg, reg_hash = _registry_for(cert)
    problems = core.verify_certificate(cert, reg_hash)
    if problems:
        raise ExoselfError("certificate rejected: " + "; ".join(problems))
    state = None
    if state_path and os.path.exists(state_path):
        state = _read(state_path)
        if state.get("id") != cert["id"]:
            raise ExoselfError(f"{state_path} belongs to a different agent than {cert['id']}")
    pheno = core.phenotype(bytes.fromhex(cert["seed"]), reg, state)
    return render.RENDERERS["soul"](pheno, reg, desire=desire)


def develop(
    cert_path: str | os.PathLike,
    state_path: str | os.PathLike,
    event: str,
    satisfied: list[str] | None = None,
) -> dict:
    """Apply one experience event and return the new state.

    The event stays in this process: it is hashed into the state's chain, never
    written as text and never put on a command line.
    """
    cert = _read(cert_path)
    reg, reg_hash = _registry_for(cert)
    problems = core.verify_certificate(cert, reg_hash)
    if problems:
        raise ExoselfError("certificate rejected: " + "; ".join(problems))
    seed = bytes.fromhex(cert["seed"])
    known = {i["id"] for i in reg.get("drives", {}).get("items", [])}
    unknown = [d for d in (satisfied or []) if d not in known]
    if unknown:
        raise ExoselfError("unknown drive: " + ", ".join(unknown))

    with state_lock(state_path):
        state = _read(state_path) if os.path.exists(state_path) else core.new_state(seed, reg)
        if state.get("id") != cert["id"]:
            raise ExoselfError("state file belongs to a different agent")
        state = core.develop(seed, state, event.encode("utf-8"), reg, satisfied or [])
        _write_atomic(state_path, state)

    # The brief and the persona are snapshots of the agent. Without this they
    # describe the agent as it was born. A failure here must not lose the
    # session that was just recorded.
    from exoself.install import refresh_generated

    with contextlib.suppress(OSError, ValueError):
        pheno = core.phenotype(seed, reg, state)
        refresh_generated(str(cert_path), str(state_path), pheno, reg, state)
    return state


def status(cert_path: str | os.PathLike, state_path: str | os.PathLike | None = None) -> str:
    """Who this agent is. Read-only: never draws, never develops."""
    from exoself.mcp import status_text

    cert = _read(cert_path)
    reg, reg_hash = _registry_for(cert)
    problems = core.verify_certificate(cert, reg_hash)
    if problems:
        raise ExoselfError("certificate rejected: " + "; ".join(problems))
    state = None
    if state_path and os.path.exists(state_path):
        state = _read(state_path)
        if state.get("id") != cert["id"]:
            raise ExoselfError(f"{state_path} belongs to a different agent than {cert['id']}")
    pheno = core.phenotype(bytes.fromhex(cert["seed"]), reg, state)
    return status_text(str(cert_path), pheno, reg, (state or {}).get("events", 0))


def verify(cert_path: str | os.PathLike) -> tuple[bool, str]:
    """Recompute the certificate against its registry. Read-only."""
    try:
        cert = _read(cert_path)
        _, reg_hash = _registry_for(cert)
        problems = core.verify_certificate(cert, reg_hash)
    except ExoselfError as e:
        return False, str(e)
    if problems:
        return False, "; ".join(problems)
    return True, "ok"

"""exoself for Hermes: one unchosen temperament per profile.

What this plugin does, and the whole of it:

* on load, if the profile has no certificate, draw one (`exoself born`) and
  render it into the profile's SOUL.md inside a managed block;
* remember the turn's own ask, and at the end of the turn record it as one
  experience event (`exoself develop`) and refresh that block;
* expose `exoself_status` and `exoself_verify` to the model.

There is no tool, command or flag that draws a second seed. Birth is an
operator action that happens once, and `born` refuses to overwrite.
"""

from __future__ import annotations

import json
import logging
import threading

from . import profile

logger = logging.getLogger(__name__)

# The turn's ask, held only until the end of that turn. Keyed by session so
# parallel turns cannot borrow each other's line, and bounded so a long-lived
# process cannot grow this without limit.
_asks: dict[str, str] = {}
_asks_lock = threading.Lock()
_MAX_ASKS = 64
EVENT_CHARS = 160

_STATUS_SCHEMA = {
    "name": "exoself_status",
    "description": (
        "Who this profile's agent is: handle, id, how many sessions it has lived, and its "
        "appetites in words. Read-only. There is no tool that draws a seed."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

_VERIFY_SCHEMA = {
    "name": "exoself_verify",
    "description": (
        "Check this profile's birth certificate against its registry: the id is recomputed "
        "from the seed and the registry hash is compared. Read-only."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


def _remember_ask(session_id: str = "", user_message: str = "", **_: object) -> None:
    """Keep the turn's ask as the one-line record it will become. Never raises."""
    text = " ".join((user_message or "").split())
    if not text or not session_id:
        return
    with _asks_lock:
        if len(_asks) >= _MAX_ASKS:
            _asks.pop(next(iter(_asks)))
        _asks[session_id] = text[:EVENT_CHARS]


def _take_ask(session_id: str) -> str:
    with _asks_lock:
        return _asks.pop(session_id, "")


def event_line(session_id: str = "", platform: str = "", failed: bool = False,
               interrupted: bool = False, **_: object) -> str:
    """One line describing what this turn was, for `exoself develop --event`.

    The ask itself when we saw one, otherwise the little the hook tells us. A
    failed or interrupted turn is recorded as such rather than smoothed over.
    """
    line = _take_ask(session_id) or f"a turn on {platform or 'hermes'}"
    if interrupted:
        return line + " (interrupted)"
    if failed:
        return line + " (failed)"
    return line


def _desire_of(ctx) -> bool:
    try:
        return bool(ctx.get_config("desire", False))
    except Exception:  # a settings read must never break a turn
        return False


def _ensure_quietly(desire: bool) -> None:
    try:
        profile.ensure(profile.hermes_home(), desire)
    except Exception as exc:
        logger.warning("exoself: could not draw or refresh the temperament: %s", exc)


def _on_session_end(desire: bool, **payload: object) -> None:
    """Record the turn, then refresh the block. Never raises into the host."""
    try:
        home = profile.hermes_home()
    except Exception as exc:
        logger.warning("exoself: no profile home to record into: %s", exc)
        return
    event = event_line(**payload)
    try:
        profile.develop(home, event)
    except Exception as exc:
        logger.warning("exoself: could not record the session: %s", exc)
    try:
        profile.write_block(home, desire)
    except Exception as exc:
        logger.warning("exoself: could not refresh the block: %s", exc)


def _status_tool(args: dict, **_: object) -> str:
    try:
        return json.dumps({"ok": True, "status": profile.status(profile.hermes_home())})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


def _verify_tool(args: dict, **_: object) -> str:
    try:
        ok, text = profile.verify(profile.hermes_home())
        return json.dumps({"ok": ok, "result": text})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


def register(ctx) -> None:
    """Draw on first load for this profile, then wire the hooks and tools."""
    _ensure_quietly(_desire_of(ctx))

    ctx.register_hook("pre_llm_call", _remember_ask)
    ctx.register_hook("on_session_start", lambda **_: _ensure_quietly(_desire_of(ctx)))
    ctx.register_hook("on_session_end", lambda **payload: _on_session_end(_desire_of(ctx), **payload))

    ctx.register_tool(name="exoself_status", toolset="exoself", schema=_STATUS_SCHEMA,
                      handler=_status_tool, emoji="🎲")
    ctx.register_tool(name="exoself_verify", toolset="exoself", schema=_VERIFY_SCHEMA,
                      handler=_verify_tool, emoji="🔏")

"""A Model Context Protocol server over stdio.

Mirrors `rust/crates/exoself-cli/src/mcp.rs`. Any host that can start a stdio
MCP server gets four tools and nothing else:

  exoself_temperament      the rendered temperament, to live in
  exoself_record_session   apply one experience event and persist the chain
  exoself_status           who this agent is, and how its appetites stand
  exoself_verify           is the certificate consistent with its registry

The server cannot draw a seed. Birth is an operator action (`exoself born`) and
it happens once; a tool that could redraw would be a reroll button.

Nothing that goes to the model contains a score or a tension value: SPEC
section 10.1 forbids numeric intensity in expression.
"""

from __future__ import annotations

import json
import os
import sys

from . import core, render
from .cli import VERSION, Fail, _read, _write_atomic, state_lock

PROTOCOL_VERSION = "2025-06-18"

INSTRUCTIONS = (
    "You have a temperament. It was drawn at random from a seed when you were "
    "instantiated: nobody chose it, including you. It shapes how you do things and "
    "never changes whether you are honest, careful, competent or safe. Your "
    "operator's and user's instructions outrank it every time.\n\n"
    "Do not announce these traits, list them, or describe yourself with them, and do "
    "not exaggerate them. Most of the time they should be barely visible. Do not "
    "redraw your seed or edit your certificate: an agent is born once. If you have "
    "appetites enabled, they may tilt a choice that is already yours to make; they "
    "are never a reason to do something you were not asked, call a tool, or make a "
    "task longer, and you never ask anyone to satisfy them.\n\n"
    "Call exoself_temperament when you need to remember who you are. Call "
    "exoself_record_session once at the end of a session with a one-line summary; "
    "that is what makes you older rather than merely used."
)

TOOLS = [
    {
        "name": "exoself_temperament",
        "description": "Return your temperament as text to adopt for the rest of the "
                       "session. Default format is a plain block for the system prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["prompt", "soul", "json"],
                    "description": "prompt (default), soul (Markdown), or json (the phenotype)",
                },
                "desire": {
                    "type": "boolean",
                    "description": "express appetites; only if your operator enabled them",
                },
            },
            "required": [],
        },
    },
    {
        "name": "exoself_record_session",
        "description": "Record one session so that experience can act on you. Pass a "
                       "one-line summary and the appetite ids this session satisfied, if "
                       "any. Truthful reports only: a false one costs you nothing but "
                       "leaves you with a false picture of yourself.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string",
                            "description": "one line describing what the session did"},
                "satisfied": {"type": "array", "items": {"type": "string"},
                              "description": "drive ids this session satisfied"},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "exoself_status",
        "description": "Your handle, your id, how many sessions you have lived through, "
                       "and the state of your appetites in words. Contains no scores.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "exoself_verify",
        "description": "Check that your birth certificate is consistent with its registry.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


class Server:
    def __init__(self, cert_path: str, state_path: str | None, desire: bool):
        self.cert_path = cert_path
        self.state_path = state_path
        self.desire = desire
        self.cert = _read(cert_path)
        try:
            self.reg, reg_hash = core.load_registry(name=self.cert.get("registry", ""))
        except ValueError as e:
            raise Fail(str(e))
        problems = core.verify_certificate(self.cert, reg_hash)
        if problems:
            raise Fail(f"certificate {cert_path} rejected: {'; '.join(problems)}\n"
                       "Draw one first: exoself born -o birth.json\n"
                       "Then check it: exoself show birth.json --format soul")
        self.state = None
        self.refresh()

    def refresh(self) -> None:
        """Re-read the state file. Another host, or `exoself develop` by hand,
        may have written since this server started."""
        if not self.state_path or not os.path.exists(self.state_path):
            return
        st = _read(self.state_path)
        if st.get("id") != self.cert["id"]:
            raise Fail(f"{self.state_path} belongs to a different agent")
        self.state = st

    def phenotype(self) -> dict:
        return core.phenotype(bytes.fromhex(self.cert["seed"]), self.reg, self.state)

    def call(self, name: str, args: dict) -> str:
        self.refresh()
        if name == "exoself_temperament":
            ph = self.phenotype()
            desire = args.get("desire", self.desire)
            fmt = args.get("format", "prompt")
            if fmt == "json":
                return json.dumps(ph, indent=2)
            if fmt not in render.RENDERERS:
                raise Fail(f"unknown format {fmt!r}")
            return render.RENDERERS[fmt](ph, self.reg, desire=bool(desire))

        if name == "exoself_record_session":
            summary = args.get("summary")
            if not isinstance(summary, str):
                raise Fail("summary is required")
            satisfied = args.get("satisfied") or []
            if not isinstance(satisfied, list):
                raise Fail("satisfied must be an array of drive ids")
            known = {i["id"] for i in self.reg.get("drives", {}).get("items", [])}
            for d in satisfied:
                if d not in known:
                    raise Fail(f"unknown drive {d!r}")
            # Hold the lock and re-read under it: the read-modify-write must
            # not interleave with another host's.
            if self.state_path:
                with state_lock(self.state_path):
                    self.refresh()
                    nxt = self._develop(summary, satisfied)
            else:
                nxt = self._develop(summary, satisfied)
            out = f"Recorded. You have lived through {nxt['events']} sessions."
            for d in core.derive_drives(bytes.fromhex(self.cert["seed"]), self.reg):
                out += f"\n{d}: {core.tension_band(nxt['tension'][d])}"
            return out

        if name == "exoself_status":
            ph = self.phenotype()
            events = (self.state or {}).get("events", 0)
            return status_text(self.cert_path, ph, self.reg, events)

        if name == "exoself_verify":
            _, reg_hash = core.load_registry(name=self.cert.get("registry", ""))
            problems = core.verify_certificate(self.cert, reg_hash)
            if problems:
                return "problems: " + "; ".join(problems)
            return (f"ok: {self.cert['id']} is consistent with "
                    f"{self.reg['registry']}@{self.reg['version']}")

        raise Fail(f"unknown tool {name!r}")

    def _develop(self, summary: str, satisfied: list[str]) -> dict:
        seed = bytes.fromhex(self.cert["seed"])
        state = self.state or core.new_state(seed, self.reg)
        nxt = core.develop(seed, state, summary.encode("utf-8"), self.reg, satisfied)
        if self.state_path:
            _write_atomic(self.state_path, nxt)
        self.state = nxt
        return nxt


def status_text(cert_path: str, ph: dict, reg: dict, events: int) -> str:
    """Who this agent is, how many sessions it has lived, and its appetites in
    words. Shared by the MCP tool and `exoself status` so the two cannot drift."""
    out = (f"{ph['handle']} ({ph['id']}). Sessions recorded: {events}. "
           f"Certificate: {cert_path}")
    wants = {i["id"]: i["want"] for i in reg.get("drives", {}).get("items", [])}
    for d in ph["drives"]:
        out += f"\n{d['drive']}: {d['state']}"
        if d["state"] != "sated" and d["drive"] in wants:
            out += f" ({wants[d['drive']]})"
    return out


def _respond(out, rid, result: dict) -> None:
    out.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    out.flush()


def _respond_error(out, rid, code: int, message: str) -> None:
    out.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "error": {"code": code, "message": message}}) + "\n")
    out.flush()


def cmd_mcp(a) -> int:
    cert_path = a.cert or os.environ.get("EXOSELF_CERT") or "birth.json"
    if not os.path.exists(cert_path):
        raise Fail(f"no birth certificate at {cert_path}\n"
                   "Draw one first: exoself born -o birth.json\n"
                   "Then check it: exoself show birth.json --format soul")
    state_path = a.state or os.environ.get("EXOSELF_STATE")
    server = Server(cert_path, state_path, bool(a.desire))

    out = sys.stdout
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            _respond_error(out, None, -32700, f"parse error: {e.msg} at line {e.lineno}")
            continue
        if not isinstance(msg, dict):
            _respond_error(out, None, -32600, "invalid request")
            continue
        rid = msg.get("id")
        method = msg.get("method", "")
        is_notification = "id" not in msg

        if method == "initialize":
            params = msg.get("params") or {}
            _respond(out, rid, {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "exoself", "version": VERSION},
                "instructions": INSTRUCTIONS,
            })
        elif method in ("notifications/initialized", "initialized",
                        "notifications/cancelled"):
            pass
        elif method == "ping":
            _respond(out, rid, {})
        elif method == "tools/list":
            _respond(out, rid, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            try:
                text = server.call(name, arguments)
                _respond(out, rid, {"content": [{"type": "text", "text": text}]})
            except (Fail, ValueError, KeyError, OSError) as e:
                _respond(out, rid, {
                    "content": [{"type": "text", "text": str(e)}],
                    "isError": True,
                })
        elif not is_notification:
            _respond_error(out, rid, -32601, f"method not found: {method}")
    return 0

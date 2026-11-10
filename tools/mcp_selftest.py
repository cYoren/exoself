#!/usr/bin/env python3
"""End-to-end check of the stdio MCP server against a real subprocess.

This is the harness-agnostic proof: it speaks the same JSON-RPC a host speaks,
so if this passes, a host that implements MCP correctly can drive the server.
It deliberately uses no MCP SDK, only stdin/stdout, so it also documents the
protocol shape for anyone wiring a host by hand.

    python3 tools/mcp_selftest.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXE = ROOT / "rust" / "target" / "release" / "exoself"


def send(proc: subprocess.Popen, msg: dict) -> None:
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()


def recv(proc: subprocess.Popen) -> dict:
    line = proc.stdout.readline()
    if not line:
        raise SystemExit(f"server closed the connection: {proc.stderr.read().decode()}")
    return json.loads(line)


def call(proc: subprocess.Popen, rid: int, method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    send(proc, msg)
    return recv(proc)


def main() -> int:
    if not EXE.exists():
        raise SystemExit(f"build it first: cargo build --release (no binary at {EXE})")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        born = subprocess.run([str(EXE), "born", "-o", str(tmp / "birth.json")],
                              capture_output=True)
        if born.returncode != 0:
            raise SystemExit(born.stderr.decode())
        print(born.stdout.decode().strip())

        proc = subprocess.Popen(
            [str(EXE), "mcp", "--cert", str(tmp / "birth.json"),
             "--state", str(tmp / "state.json")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        init = call(proc, 1, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "selftest", "version": "0"},
        })
        result = init["result"]
        assert result["serverInfo"]["name"] == "exoself", result
        assert "tools" in result["capabilities"], result
        assert "born once" in result["instructions"], result
        print("initialize ok, protocol", result["protocolVersion"])

        # A notification must not produce a response.
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        tools = call(proc, 2, "tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        assert names == ["exoself_temperament", "exoself_record_session",
                         "exoself_status", "exoself_verify"], names
        for t in tools:
            assert t["inputSchema"]["type"] == "object", t
        print("tools/list ok:", ", ".join(names))

        r = call(proc, 3, "tools/call", {"name": "exoself_temperament", "arguments": {}})
        text = r["result"]["content"][0]["text"]
        assert "take precedence over everything here" in text, text
        assert "Lately you are taken" not in text, "desire is on by default"
        assert not any(c.isdigit() for c in text.replace("#", "")), "digits in expression"
        print("temperament ok,", len(text), "chars, desire off by default")

        r = call(proc, 4, "tools/call", {
            "name": "exoself_temperament", "arguments": {"format": "soul", "desire": True}})
        soul = r["result"]["content"][0]["text"]
        assert soul.startswith("# "), soul[:40]
        assert "## Wants" in soul, soul[:400]
        print("soul with appetites ok")

        status = call(proc, 5, "tools/call", {"name": "exoself_status", "arguments": {}})["result"]["content"][0]["text"]
        print("status:", status.replace("\n", " | ")[:120])

        for i in range(3):
            r = call(proc, 10 + i, "tools/call", {
                "name": "exoself_record_session",
                "arguments": {"summary": f"selftest session {i}"}})
            assert not r["result"].get("isError"), r
        print("record_session x3 ok:",
              r["result"]["content"][0]["text"].replace("\n", " | "))

        state = json.loads((tmp / "state.json").read_text())
        assert state["events"] == 3, state
        assert len(state["chain"]) == 64, state
        assert state["tension"], state
        print("state persisted:", state["events"], "events, chain", state["chain"][:12] + "...")

        # A session recorded by another host (or by `exoself develop` by hand)
        # must not be erased by this server's cached copy of the state.
        subprocess.run([str(EXE), "develop", str(tmp / "birth.json"),
                        str(tmp / "state.json"), "--event", "from another host"],
                       check=True, capture_output=True)
        r = call(proc, 15, "tools/call", {
            "name": "exoself_record_session",
            "arguments": {"summary": "after another host wrote"}})
        assert not r["result"].get("isError"), r
        state = json.loads((tmp / "state.json").read_text())
        assert state["events"] == 5, f"lost an event: {state['events']} != 5"
        print("a concurrent writer is not erased:", state["events"], "events")

        # An unknown drive must fail loudly instead of silently polluting the chain.
        r = call(proc, 20, "tools/call", {
            "name": "exoself_record_session",
            "arguments": {"summary": "x", "satisfied": ["not-a-drive"]}})
        assert r["result"].get("isError"), r
        print("bad drive rejected ok")

        r = call(proc, 21, "tools/call", {"name": "exoself_verify", "arguments": {}})
        assert r["result"]["content"][0]["text"].startswith("ok:"), r
        print("verify ok")

        r = call(proc, 22, "nope/nothing")
        assert r["error"]["code"] == -32601, r
        print("unknown method rejected ok")

        proc.stdin.close()
        proc.wait(timeout=5)

    print("\nmcp selftest passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

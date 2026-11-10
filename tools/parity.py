#!/usr/bin/env python3
"""Cross-implementation parity: the Rust port against the Python reference.

The spec says two implementations conform if they produce equal phenotypes for
the same seed, registry and state. This script checks something stronger: that
the Rust binary produces byte-identical *rendered text* and byte-identical JSON
for hundreds of seeds, with and without development events.

    python3 tools/parity.py                  # 120 seeds, three formats
    python3 tools/parity.py --seeds 500

Exit code is non-zero on the first disagreement.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from exoself import core  # noqa: E402

FORMATS = ["prompt", "soul", "json"]


def python_show(cert_path: pathlib.Path, fmt: str, state: pathlib.Path | None, desire: bool) -> bytes:
    args = ["show", str(cert_path), "--format", fmt]
    if state is not None:
        args += ["--state", str(state)]
    if desire:
        args.append("--desire")
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; from exoself.cli import main; sys.exit(main(sys.argv[1:]))", *args],
        capture_output=True,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )
    if out.returncode != 0:
        raise SystemExit(f"python show failed: {out.stderr.decode()}")
    return out.stdout


def rust_show(exe: pathlib.Path, cert_path: pathlib.Path, fmt: str,
              state: pathlib.Path | None, desire: bool) -> bytes:
    args = [str(exe), "show", str(cert_path), "--format", fmt]
    if state is not None:
        args += ["--state", str(state)]
    if desire:
        args.append("--desire")
    out = subprocess.run(args, capture_output=True)
    if out.returncode != 0:
        raise SystemExit(f"rust show failed: {out.stderr.decode()}")
    return out.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=120)
    ap.add_argument("--exe", default=str(ROOT / "rust" / "target" / "release" / "exoself"))
    ap.add_argument("--events", type=int, default=40)
    a = ap.parse_args()

    exe = pathlib.Path(a.exe)
    if not exe.exists():
        raise SystemExit(f"build it first: cargo build --release  (no binary at {exe})")

    reg, reg_hash = core.load_registry()
    checked = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        for i in range(a.seeds):
            seed = core.stream(bytes(32), f"parity/{i}", 32)
            cert = core.born(reg_hash, seed=seed)
            cert_path = tmp / "birth.json"
            cert_path.write_text(json.dumps(cert, indent=2) + "\n")

            for fmt in FORMATS:
                for desire in (False, True):
                    want = python_show(cert_path, fmt, None, desire)
                    got = rust_show(exe, cert_path, fmt, None, desire)
                    if want != got:
                        sys.stderr.write(
                            f"MISMATCH seed {i} format {fmt} desire={desire}\n"
                            f"--- python ---\n{want.decode()}\n--- rust ---\n{got.decode()}\n")
                        return 1
                    checked += 1

            # Development: the same events through both implementations.
            py_state = core.new_state(seed, reg)
            held = core.derive_drives(seed, reg)
            log = []
            for e in range(a.events):
                met = [held[e % len(held)]] if e % 6 == 5 else []
                event = f"session {e}: parity check".encode()
                py_state = core.develop(seed, py_state, event, reg, met)
                log.append({"event": event.decode(), "satisfied": met})
            state_path = tmp / "state.json"
            state_path.write_text(json.dumps(py_state, indent=2) + "\n")

            want = python_show(cert_path, "json", state_path, True)
            got = rust_show(exe, cert_path, "json", state_path, True)
            if want != got:
                sys.stderr.write(f"MISMATCH after {a.events} events, seed {i}\n")
                sys.stderr.write(want.decode()[:800] + "\n")
                sys.stderr.write(got.decode()[:800] + "\n")
                return 1
            checked += 1

            # And the Rust binary must arrive at the same state when it replays
            # the same events from scratch.
            rust_state = tmp / "rust-state.json"
            if rust_state.exists():
                rust_state.unlink()
            for entry in log:
                args = [str(exe), "develop", str(cert_path), str(rust_state),
                        "--event", entry["event"]]
                for d in entry["satisfied"]:
                    args += ["--satisfied", d]
                out = subprocess.run(args, capture_output=True)
                if out.returncode != 0:
                    sys.stderr.write(out.stderr.decode())
                    return 1
            if json.loads(rust_state.read_text()) != py_state:
                sys.stderr.write(f"MISMATCH state after replay, seed {i}\n")
                return 1
            checked += 1

    print(f"parity ok: {checked} comparisons over {a.seeds} seeds, "
          f"{a.events} events each")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

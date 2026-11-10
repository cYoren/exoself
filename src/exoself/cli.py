"""Command line interface: exoself born | show | develop | verify | conformance."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import core, render


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_new(path: str, data: dict) -> None:
    # Mode "x" fails if the file exists. A birth certificate is never overwritten.
    with open(path, "x", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def cmd_born(a) -> int:
    reg, reg_hash = core.load_registry(a.registry)
    if os.path.exists(a.out):
        print(f"{a.out} already exists. An agent is born once; refusing to reroll.", file=sys.stderr)
        return 1
    cert = core.born(reg_hash, f"{reg['registry']}@{reg['version']}")
    _write_new(a.out, cert)
    pheno = core.phenotype(bytes.fromhex(cert["seed"]), reg)
    print(f"{pheno['handle']} ({cert['id']}) written to {a.out}")
    return 0


def cmd_show(a) -> int:
    reg, reg_hash = core.load_registry(a.registry)
    cert = _read(a.cert)
    problems = core.verify_certificate(cert, reg_hash)
    if problems:
        print("certificate rejected: " + "; ".join(problems), file=sys.stderr)
        return 1
    drift = _read(a.state)["drift"] if a.state else None
    pheno = core.phenotype(bytes.fromhex(cert["seed"]), reg, drift)
    if a.format == "json":
        print(json.dumps(pheno, indent=2))
    else:
        sys.stdout.write(render.RENDERERS[a.format](pheno, reg))
    return 0


def cmd_develop(a) -> int:
    reg, _ = core.load_registry(a.registry)
    cert = _read(a.cert)
    seed = bytes.fromhex(cert["seed"])
    state = _read(a.state) if os.path.exists(a.state) else core.new_state(seed)
    if state["id"] != cert["id"]:
        print("state file belongs to a different agent", file=sys.stderr)
        return 1
    state = core.develop(state, a.event.encode("utf-8"), reg)
    with open(a.state, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    print(f"event {state['events']} applied, drift now {state['drift']}")
    return 0


def cmd_verify(a) -> int:
    _, reg_hash = core.load_registry(a.registry)
    problems = core.verify_certificate(_read(a.cert), reg_hash)
    for p in problems:
        print("FAIL " + p)
    if not problems:
        print("ok")
    return 1 if problems else 0


def cmd_conformance(a) -> int:
    reg, reg_hash = core.load_registry(a.registry)
    vectors = _read(a.vectors)
    if vectors["registry_sha256"] != reg_hash:
        print("vectors were generated for a different registry", file=sys.stderr)
        return 1
    failed = 0
    for v in vectors["vectors"]:
        got = core.phenotype(bytes.fromhex(v["seed"]), reg, v.get("drift"))
        if got != v["phenotype"]:
            failed += 1
            print(f"FAIL seed {v['seed'][:16]}...")
    print(f"{len(vectors['vectors']) - failed}/{len(vectors['vectors'])} vectors pass")
    return 1 if failed else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="exoself", description=__doc__)
    p.add_argument("--registry", help="path to a trait registry (default: bundled core@0.1)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("born", help="draw a seed and write a birth certificate")
    s.add_argument("-o", "--out", default="birth.json")
    s.set_defaults(fn=cmd_born)

    s = sub.add_parser("show", help="derive and render the phenotype")
    s.add_argument("cert")
    s.add_argument("--state", help="development state file")
    s.add_argument("--format", choices=["json", "prompt", "soul"], default="prompt")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("develop", help="apply one experience event")
    s.add_argument("cert")
    s.add_argument("state")
    s.add_argument("--event", required=True)
    s.set_defaults(fn=cmd_develop)

    s = sub.add_parser("verify", help="check a birth certificate")
    s.add_argument("cert")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("conformance", help="check this implementation against test vectors")
    s.add_argument("vectors")
    s.set_defaults(fn=cmd_conformance)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())

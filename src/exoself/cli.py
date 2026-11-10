"""Command line interface: exoself born | show | develop | verify | conformance."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time

from . import core, render

LOCK_WAIT = 10.0
LOCK_STALE = 60.0


@contextlib.contextmanager
def state_lock(state_path: str):
    """Hold a sibling lock file for a read-modify-write of the state.

    Two hosts can record sessions for the same agent, and development is
    read-modify-write on a chained file, so without this the later writer
    silently erases the earlier event. The Rust side takes the same lock.
    """
    lock = state_path + ".lock"
    started = time.monotonic()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > LOCK_STALE:
                    os.unlink(lock)  # a process died holding it
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() - started > LOCK_WAIT:
                raise SystemExit(f"{lock} is locked by another process. "
                                 "Remove it if that process is gone.")
            time.sleep(0.01)
            continue
        try:
            os.write(fd, b"%d\n" % os.getpid())
        finally:
            os.close(fd)
        break
    try:
        yield
    finally:
        # Only remove a lock that is still ours; it may have been stolen.
        with contextlib.suppress(FileNotFoundError):
            if open(lock).read().strip() == str(os.getpid()):
                os.unlink(lock)


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_new(path: str, data: dict) -> None:
    # Mode "x" fails if the file exists. A birth certificate is never overwritten.
    with open(path, "x", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _registry_for(a, cert: dict) -> tuple[dict, list[str]]:
    """Load the registry a certificate pins, or the operator's explicit override."""
    if a.registry:
        reg, reg_hash = core.load_registry(a.registry)
        problems = core.verify_certificate({**cert, "registry_sha256": reg_hash}, reg_hash)
        if cert.get("registry_sha256") != reg_hash:
            print(f"note: running {cert.get('id')} against {a.registry}, "
                  f"not the registry it was born with ({cert.get('registry')})", file=sys.stderr)
        return reg, problems
    reg, reg_hash = core.load_registry(name=cert.get("registry", ""))
    return reg, core.verify_certificate(cert, reg_hash)


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
    cert = _read(a.cert)
    try:
        reg, problems = _registry_for(a, cert)
    except ValueError as e:
        reg, problems = None, [str(e)]
    if problems:
        print("certificate rejected: " + "; ".join(problems), file=sys.stderr)
        return 1
    state = None
    if a.state:
        state = _read(a.state)
        if state.get("id") != cert["id"]:
            print(f"{a.state} belongs to a different agent than {cert['id']}",
                  file=sys.stderr)
            return 1
    else:
        # The usual mistake: the state is right there and the flag was
        # forgotten, so the agent is rendered as it was born.
        sibling = os.path.join(os.path.dirname(os.path.abspath(a.cert)), "state.json")
        if os.path.exists(sibling):
            print(f"note: {sibling} exists but --state was not given; "
                  "rendering the genome only", file=sys.stderr)
    pheno = core.phenotype(bytes.fromhex(cert["seed"]), reg, state)
    if a.format == "json":
        print(json.dumps(pheno, indent=2))
    else:
        sys.stdout.write(render.RENDERERS[a.format](pheno, reg, desire=a.desire))
    return 0


def cmd_develop(a) -> int:
    cert = _read(a.cert)
    try:
        reg, problems = _registry_for(a, cert)
    except ValueError as e:
        reg, problems = None, [str(e)]
    if problems:
        print("certificate rejected: " + "; ".join(problems), file=sys.stderr)
        return 1
    seed = bytes.fromhex(cert["seed"])
    with state_lock(a.state):
        return _develop_locked(a, cert, seed, reg)


def _develop_locked(a, cert, seed, reg) -> int:
    existed = os.path.exists(a.state)
    state = _read(a.state) if existed else core.new_state(seed, reg)
    if state["id"] != cert["id"]:
        print("state file belongs to a different agent", file=sys.stderr)
        return 1
    known = {i["id"] for i in reg.get("drives", {}).get("items", [])}
    unknown = [d for d in a.satisfied if d not in known]
    if unknown:
        print("unknown drive: " + ", ".join(unknown), file=sys.stderr)
        return 1
    state = core.develop(seed, state, a.event.encode("utf-8"), reg, a.satisfied)
    # Write beside the target and rename, so an interrupted run cannot leave a
    # half-written history behind. The Rust side does the same.
    tmp = a.state + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    os.replace(tmp, a.state)
    if not existed:
        print(f"note: {a.state} did not exist, so a new history was started", file=sys.stderr)
    drift = ", ".join(f"{k}:{v:+d}" for k, v in state["drift"].items())
    tension = ", ".join(f"{k}:{v}" for k, v in state["tension"].items())
    print(f"event {state['events']} applied, drift [{drift}], tension [{tension}]")
    return 0


def cmd_verify(a) -> int:
    cert = _read(a.cert)
    try:
        if a.registry:
            _, reg_hash = core.load_registry(a.registry)
        else:
            _, reg_hash = core.load_registry(name=cert.get("registry", ""))
        problems = core.verify_certificate(cert, reg_hash)
    except ValueError as e:
        problems = [str(e)]
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
        got = core.phenotype(bytes.fromhex(v["seed"]), reg, v.get("state"))
        if got != v["phenotype"]:
            failed += 1
            print(f"FAIL seed {v['seed'][:16]}...")
    print(f"{len(vectors['vectors']) - failed}/{len(vectors['vectors'])} vectors pass")
    return 1 if failed else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="exoself", description=__doc__)
    p.add_argument("--registry", help="path to a trait registry (default: the one the certificate pins; core@0.3 at birth)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("born", help="draw a seed and write a birth certificate")
    s.add_argument("-o", "--out", default="birth.json")
    s.set_defaults(fn=cmd_born)

    s = sub.add_parser("show", help="derive and render the phenotype")
    s.add_argument("cert")
    s.add_argument("--state", help="development state file")
    s.add_argument("--format", choices=["json", "prompt", "soul"], default="prompt")
    s.add_argument("--desire", action="store_true",
                   help="express drives and fixation (off by default, see SPEC 9.2)")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("develop", help="apply one experience event")
    s.add_argument("cert")
    s.add_argument("state")
    s.add_argument("--event", required=True)
    s.add_argument("--satisfied", action="append", default=[], metavar="DRIVE",
                   help="a drive this event satisfied; repeat the flag for more than one")
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

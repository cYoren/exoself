"""Command line interface: born | show | develop | verify | conformance |
commit | install | mcp | version.

Mirrors `rust/crates/exoself-cli`. The flags, the exit codes and the output are
the same in both, so a host can swap binaries without noticing. Rendering is
byte-identical, which `tools/parity.py` checks over a few hundred seeds.

Exit codes: 0 success, 1 a certificate or vector check failed, 2 anything else.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import sys
import time

from . import core, render

VERSION = "0.3.0"
LOCK_WAIT = 10.0
LOCK_STALE = 60.0

USAGE = """\
exoself: an unchosen temperament for software agents

  exoself born [-o birth.json] [--registry PATH]
        [--local-entropy HEX] [--beacon ID --beacon-value HEX]
        Draw a seed once and write the birth certificate. Refuses to overwrite.

  exoself show CERT [--state PATH] [--format prompt|soul|json] [--desire]
        Derive the phenotype and render it. Desire is off unless --desire.

  exoself develop CERT STATE --event TEXT [--satisfied DRIVE]...
        Apply one experience event. Writes STATE atomically.

  exoself verify CERT [--registry PATH]
        Check a birth certificate. Exit code is non-zero on any problem.

  exoself conformance [VECTORS] [--registry PATH]
        Reproduce spec/vectors.json.

  exoself commit [-o commitment.json] [--local-entropy HEX]
        Print the commitment for a level 1 (beacon-bound) birth.

  exoself install [--dir DIR] [--file AGENTS.md] [--soul SOUL.md]
        [--json-config PATH] [--desire]
        Plug an agent into a directory: certificate, SOUL.md, a managed block in
        AGENTS.md, and optionally an MCP server entry.

  exoself mcp [--cert PATH] [--state PATH] [--desire]
        Speak the Model Context Protocol on stdin/stdout. Tools:
        exoself_temperament, exoself_record_session, exoself_status,
        exoself_verify.

  exoself version

A certificate is never overwritten and a seed is never redrawn by this tool.
SPEC.md is the authority; see AGENTS.md for what an agent should do with this.
"""


class Fail(Exception):
    """An operational error. Printed without a traceback; exit code 2."""


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise Fail(f"{path}: no such file")
    except IsADirectoryError:
        raise Fail(f"{path}: is a directory")
    except json.JSONDecodeError as e:
        raise Fail(f"{path}: {e.msg} at line {e.lineno} column {e.colno}")
    except OSError as e:
        raise Fail(f"{path}: {e.strerror}")


def _write_new(path: str, data: dict) -> None:
    # Mode "x" fails if the file exists. A birth certificate is never overwritten.
    try:
        with open(path, "x", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    except FileExistsError:
        raise Fail(f"{path} already exists")
    except OSError as e:
        raise Fail(f"{path}: {e.strerror}")


def _write_atomic(path: str, data: dict) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise Fail(f"{path}: {e.strerror}")


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
                raise Fail(f"{lock} is locked by another process. "
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
        with contextlib.suppress(FileNotFoundError, OSError):
            if open(lock).read().strip() == str(os.getpid()):
                os.unlink(lock)


# --------------------------------------------------------------------------
# Registry selection
# --------------------------------------------------------------------------

def _registry_for(a, cert: dict) -> tuple[dict, list[str]]:
    """Load the registry a certificate pins, or the operator's explicit override."""
    if a.registry:
        try:
            reg, reg_hash = core.load_registry(a.registry)
        except ValueError as e:
            raise Fail(str(e))
        if cert.get("registry_sha256") != reg_hash:
            print(f"note: running {cert.get('id')} against {a.registry}, "
                  f"not the registry it was born with ({cert.get('registry')})",
                  file=sys.stderr)
        return reg, core.verify_certificate({**cert, "registry_sha256": reg_hash}, reg_hash)
    try:
        reg, reg_hash = core.load_registry(name=cert.get("registry", ""))
    except ValueError as e:
        raise Fail(str(e))
    return reg, core.verify_certificate(cert, reg_hash)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_born(a) -> int:
    if os.path.exists(a.out):
        raise Fail(f"{a.out} already exists. An agent is born once; refusing to reroll.")
    reg, reg_hash = core.load_registry(a.registry)

    beacon_value = a.beacon_value
    local_entropy = a.local_entropy
    if beacon_value and not local_entropy:
        raise Fail("--beacon-value needs --local-entropy <64 hex>, "
                   "the bytes you committed to")
    if local_entropy and not beacon_value:
        # A seed the operator chose is not a draw. SPEC 3.2 allows level 0 only
        # from the OS CSPRNG, and an unchosen seed is the point of the framework.
        raise Fail("--local-entropy only binds a seed to a beacon round. "
                   "Pass --beacon <id> and --beacon-value <hex> from the round, "
                   "or drop it and let the OS draw. See `exoself commit`.")
    if a.beacon and not beacon_value:
        raise Fail("--beacon needs --beacon-value <hex> from the round, and "
                   "--local-entropy <64 hex> from `exoself commit`")

    if beacon_value:
        local = _parse_seed_hex(local_entropy, "--local-entropy")
        try:
            beacon = bytes.fromhex(beacon_value)
        except ValueError:
            raise Fail("--beacon-value is not hex")
        seed = core.bind_beacon(local, beacon)
        attestation = {
            "level": 1,
            "commitment": hashlib.sha256(local).hexdigest(),
            "local_entropy": local.hex(),
            "beacon": a.beacon,
            "beacon_value": beacon_value.lower(),
        }
        cert = core.born(reg_hash, f"{reg['registry']}@{reg['version']}",
                         seed=seed, attestation=attestation)
    else:
        cert = core.born(reg_hash, f"{reg['registry']}@{reg['version']}")

    _write_new(a.out, cert)
    pheno = core.phenotype(bytes.fromhex(cert["seed"]), reg)
    print(f"{pheno['handle']} ({cert['id']}) written to {a.out}")
    return 0


def _parse_seed_hex(value: str, flag: str) -> bytes:
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        raise Fail(f"{flag} is not hex")
    if len(raw) != core.SEED_BYTES:
        raise Fail(f"{flag} must be {core.SEED_BYTES} bytes of hex, got {len(raw)}")
    return raw


def cmd_show(a) -> int:
    cert = _read(a.cert)
    reg, problems = _registry_for(a, cert)
    if problems:
        raise Fail("certificate rejected: " + "; ".join(problems))
    state = None
    if a.state:
        state = _read(a.state)
        if state.get("id") != cert["id"]:
            raise Fail(f"{a.state} belongs to a different agent than {cert['id']}")
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
    reg, problems = _registry_for(a, cert)
    if problems:
        raise Fail("certificate rejected: " + "; ".join(problems))
    seed = bytes.fromhex(cert["seed"])
    known = {i["id"] for i in reg.get("drives", {}).get("items", [])}
    unknown = [d for d in a.satisfied if d not in known]
    if unknown:
        raise Fail("unknown drive: " + ", ".join(unknown))

    with state_lock(a.state):
        existed = os.path.exists(a.state)
        state = _read(a.state) if existed else core.new_state(seed, reg)
        if state.get("id") != cert["id"]:
            raise Fail("state file belongs to a different agent")
        state = core.develop(seed, state, a.event.encode("utf-8"), reg, a.satisfied)
        _write_atomic(a.state, state)
    if not existed:
        print(f"note: {a.state} did not exist, so a new history was started",
              file=sys.stderr)
    drift = ", ".join(f"{k}:{v:+d}" for k, v in state["drift"].items())
    tension = ", ".join(f"{k}:{v}" for k, v in state["tension"].items())
    print(f"event {state['events']} applied, drift [{drift}], tension [{tension}]")
    return 0


def cmd_verify(a) -> int:
    try:
        cert = _read(a.cert)
    except Fail as e:
        print(f"FAIL {e}")
        return 1
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


def _default_vectors() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (
        os.path.join(os.getcwd(), "spec", "vectors.json"),
        os.path.join(here, "..", "..", "spec", "vectors.json"),
    ):
        if os.path.exists(candidate):
            return candidate
    raise Fail("no spec/vectors.json here; pass the path: "
               "exoself conformance <vectors.json>")


def cmd_conformance(a) -> int:
    reg, reg_hash = core.load_registry(a.registry)
    vectors = _read(a.vectors or _default_vectors())
    if vectors["registry_sha256"] != reg_hash:
        raise Fail("vectors were generated for a different registry")
    failed = 0
    total, complaints = core.check_primitives(vectors, reg)
    for complaint in complaints:
        failed += 1
        print(f"FAIL {complaint}")
    total += len(vectors["vectors"])
    for v in vectors["vectors"]:
        got = core.phenotype(bytes.fromhex(v["seed"]), reg, v.get("state"))
        if got != v["phenotype"]:
            failed += 1
            print(f"FAIL seed {v['seed'][:16]}...")
    print(f"{total - failed}/{total} vectors pass")
    return 1 if failed else 0


def cmd_commit(a) -> int:
    supplied = _parse_seed_hex(a.local_entropy, "--local-entropy") if a.local_entropy else None
    local = supplied or os.urandom(core.SEED_BYTES)
    commitment = hashlib.sha256(local).hexdigest()
    out = a.out

    secret_path = None
    if supplied is None:
        if a.secret_out:
            secret_path = a.secret_out
        elif out.endswith(".json"):
            secret_path = out[:-len(".json")] + ".secret.json"
        else:
            secret_path = out + ".secret.json"
        secret = {
            "commitment": commitment,
            "local_entropy": local.hex(),
            "note": "do not publish this file before the beacon round",
        }
        _write_secret(secret_path, secret)

    if secret_path:
        nxt = (f"publish this file. The entropy that opens it is in {secret_path} "
               "and is not for publication until the beacon round closes: "
               "exoself born --beacon <id> --beacon-value <hex> "
               "--local-entropy <hex>")
    else:
        nxt = ("publish this file, then: exoself born --beacon <id> "
               "--beacon-value <hex> --local-entropy <hex you supplied>")
    record = {
        "commitment": commitment,
        "committed_at": datetime.datetime.now(datetime.timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "next": nxt,
    }
    _write_new(out, record)
    print(f"commitment {commitment} written to {out}")
    if secret_path:
        print(f"entropy kept in {secret_path} (mode 0600, do not publish)")
    else:
        print("entropy was supplied by you, so nothing was written twice")
    return 0


def _write_secret(path: str, data: dict) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise Fail(f"{path} already exists. Refusing to overwrite a commitment.")
    except OSError as e:
        raise Fail(f"{path}: {e.strerror}")
    try:
        os.write(fd, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def cmd_install(a) -> int:
    from .install import cmd_install as run
    return run(a)


def cmd_mcp(a) -> int:
    from .mcp import cmd_mcp as run
    return run(a)


def cmd_version(a) -> int:
    print(f"exoself {VERSION} (spec {core.SPEC_VERSION})")
    return 0


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="exoself", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # Abbreviations would make `--form` mean `--format`, which is the kind of
    # silence this tool refuses everywhere else.
    p.add_argument("-V", "--version", action="version",
                   version=f"exoself {VERSION} (spec {core.SPEC_VERSION})")
    sub = p.add_subparsers(dest="cmd", required=True)

    def command(name: str, help_text: str, fn) -> argparse.ArgumentParser:
        s = sub.add_parser(name, help=help_text, allow_abbrev=False)
        s.set_defaults(fn=fn)
        return s

    s = command("born", "draw a seed and write a birth certificate", cmd_born)
    s.add_argument("-o", "--out", default="birth.json")
    s.add_argument("--registry", help="path to a trait registry")
    s.add_argument("--local-entropy", metavar="HEX",
                   help="the 32 bytes you committed to; needs --beacon and --beacon-value")
    s.add_argument("--beacon", metavar="ID", help="beacon identifier, e.g. drand:quicknet:1234")
    s.add_argument("--beacon-value", metavar="HEX", help="the beacon round's output")

    s = command("show", "derive and render the phenotype", cmd_show)
    s.add_argument("cert")
    s.add_argument("--state", help="development state file")
    s.add_argument("--format", choices=["json", "prompt", "soul"], default="prompt")
    s.add_argument("--desire", action="store_true",
                   help="express drives and fixation (off by default, see SPEC 9.2)")
    s.add_argument("--registry", help="path to a trait registry")

    s = command("develop", "apply one experience event", cmd_develop)
    s.add_argument("cert")
    s.add_argument("state")
    s.add_argument("--event", required=True)
    s.add_argument("--satisfied", action="append", default=[], metavar="DRIVE",
                   help="a drive this event satisfied; repeat the flag for more than one")
    s.add_argument("--registry", help="path to a trait registry")

    s = command("verify", "check a birth certificate", cmd_verify)
    s.add_argument("cert")
    s.add_argument("--registry", help="path to a trait registry")

    s = command("conformance", "check this implementation against test vectors",
                cmd_conformance)
    s.add_argument("vectors", nargs="?", help="default: spec/vectors.json")
    s.add_argument("--registry", help="path to a trait registry")

    s = command("commit", "print the commitment for a level 1 (beacon-bound) birth",
                cmd_commit)
    s.add_argument("-o", "--out", default="commitment.json")
    s.add_argument("--local-entropy", metavar="HEX", help="use entropy you already have")
    s.add_argument("--secret-out", metavar="PATH", help="where to keep the pre-image")

    s = command("install", "plug an agent into a directory", cmd_install)
    s.add_argument("--dir", default=".")
    s.add_argument("--file", default="AGENTS.md", help="the project brief to add a block to")
    s.add_argument("--soul", default="SOUL.md", help="the persona file to write")
    s.add_argument("--json-config", help="an mcpServers config to add an entry to")
    s.add_argument("--desire", action="store_true", help="express appetites (SPEC 9.2)")
    s.add_argument("--registry", help="path to a trait registry")

    s = command("mcp", "speak the Model Context Protocol on stdin/stdout", cmd_mcp)
    s.add_argument("--cert", help="birth certificate (default: $EXOSELF_CERT or birth.json)")
    s.add_argument("--state", help="state file (default: $EXOSELF_STATE)")
    s.add_argument("--desire", action="store_true", help="express appetites (SPEC 9.2)")

    command("version", "print the version", cmd_version)
    return p


def main(argv=None) -> int:
    if argv is None and len(sys.argv) == 1:
        print(USAGE, end="")
        return 0
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except Fail as e:
        print(str(e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

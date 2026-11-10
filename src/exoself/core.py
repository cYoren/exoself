"""Reference implementation of the exoself derivation (SPEC.md, sections 3 to 9).

Everything here is integer arithmetic over SHA-256 output, so that any
language can reproduce the same phenotype bit for bit. No floats, no
platform RNG after the seed is drawn.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from importlib import resources

SPEC_VERSION = "0.3"
SUPPORTED_VERSIONS = ("0.1", "0.2", "0.3")
# Derivation epoch, not the spec version. It changes only when an existing
# derivation changes, because changing it turns every agent into someone else.
PREFIX = b"exoself/0.1/"
SEED_BYTES = 32

GAUSS_DRAWS = 12
GAUSS_CENTER = 393210  # 12 * 65535 / 2
SCALE_DEN = 6553600  # 65536 (sd of one gaussian) * 100 (loading unit)
SCORE_MEAN = 500
SCORE_SD = 150
SCORE_MAX = 1000

DRIFT_LIMIT = 100
# A drift point that pulls a dimension back toward its genome value. See 8.3.
DRIFT_PULL = 25
# Development arithmetic, recorded in the state (SPEC 8.2). Absent means the
# 0.2 walk, so an old state file keeps replaying exactly.
DEVELOP_WALK = "walk"
DEVELOP_MEAN_REVERTING = "mean-reverting"
DEVELOP_MODES = (DEVELOP_WALK, DEVELOP_MEAN_REVERTING)
DEVELOP_DEFAULT = DEVELOP_MEAN_REVERTING
MIN_EXPRESSED = 2
MAX_EXPRESSED = 5

TENSION_MAX = 1000
TENSION_PRESENT = 250
TENSION_HUNGRY = 650


# --------------------------------------------------------------------------
# Primitives (SPEC section 4)
# --------------------------------------------------------------------------

def stream(seed: bytes, label: str, n: int = 32) -> bytes:
    """n bytes of the labeled stream: SHA-256 in counter mode."""
    if len(seed) != SEED_BYTES:
        raise ValueError("seed must be exactly 32 bytes")
    out = b""
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(
            PREFIX + seed + b"\x00" + label.encode("utf-8") + b"\x00"
            + counter.to_bytes(4, "big")
        ).digest()
        counter += 1
    return out[:n]


def u32(seed: bytes, label: str) -> int:
    return int.from_bytes(stream(seed, label, 4), "big")


def gauss(seed: bytes, label: str) -> int:
    """Centered Irwin-Hall integer: mean 0, standard deviation about 65536."""
    b = stream(seed, label, 2 * GAUSS_DRAWS)
    total = sum(int.from_bytes(b[i:i + 2], "big") for i in range(0, len(b), 2))
    return total - GAUSS_CENTER


def clamp(x: int, lo: int, hi: int) -> int:
    return lo if x < lo else hi if x > hi else x


def band(score: int) -> str:
    if score <= 199:
        return "very_low"
    if score <= 349:
        return "low"
    if score <= 650:
        return "mid"
    if score <= 800:
        return "high"
    return "very_high"


# --------------------------------------------------------------------------
# Registry (SPEC section 5)
# --------------------------------------------------------------------------

DEFAULT_REGISTRY = "core@0.3"


def load_registry(path: str | None = None, name: str = DEFAULT_REGISTRY) -> tuple[dict, str]:
    """Return (registry, sha256 hex of the exact file bytes).

    With no path, loads the bundled registry called `name`, e.g. "core@0.1".
    """
    if path is None:
        fname = name.replace("@", "-") + ".json"
        if not fname.replace("-", "").replace(".", "").isalnum():
            raise ValueError(f"bad registry name {name!r}")
        res = resources.files("exoself") / "registry" / fname
        if not res.is_file():
            raise ValueError(f"no bundled registry {name!r}")
        raw = res.read_bytes()
    else:
        with open(path, "rb") as f:
            raw = f.read()
    registry = json.loads(raw)
    validate_registry(registry)
    return registry, hashlib.sha256(raw).hexdigest()


# Words and phrases that turn an appetite into a complaint about the past. A
# `hungry` text may say the want is stronger; it may not say something is
# lacking, that time has passed, or that the agent feels the deficit.
GRIEVANCE = (
    "in a while", "lately", "has been a while", "have been", "have not", "miss it",
    "you miss", "the lack", "too much", "weighs on you", "hungry for",
    "bothers you that", "and you feel",
)


def version_at_least(version: str, want: tuple[int, ...]) -> bool:
    """Compare registry versions numerically, not as strings.

    "0.10" is later than "0.3"; a string comparison says otherwise, which would
    quietly switch the 9.2.5 grievance check off at the tenth minor version.
    """
    parts = []
    for piece in str(version).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) >= want


def grievance_words(text: str) -> list[str]:
    low = text.lower()
    return [w for w in GRIEVANCE if w in low]


def validate_registry(reg: dict) -> None:
    nf = len(reg["factors"])
    seen = set()
    for d in reg["dimensions"]:
        if d["id"] in seen:
            raise ValueError(f"duplicate dimension {d['id']}")
        seen.add(d["id"])
        if len(d["loadings"]) != nf:
            raise ValueError(f"{d['id']}: need {nf} loadings")
        ss = sum(x * x for x in d["loadings"]) + d["unique"] ** 2
        if not 9800 <= ss <= 10200:
            raise ValueError(f"{d['id']}: squared loadings sum to {ss}, need 9800..10200")
        for b in ("very_low", "low", "high", "very_high"):
            text = d["text"][b]
            if any(ch.isdigit() for ch in text):
                raise ValueError(f"{d['id']}.{b}: expression text must not contain digits")
    for name, group in reg["picks"].items():
        ids = [i["id"] for i in group["items"]]
        if len(set(ids)) != len(ids) or group["count"] > len(ids):
            raise ValueError(f"picks.{name}: bad ids or count")
    for q in reg["quirks"]:
        if not 0 <= q["permille"] <= 1000:
            raise ValueError(f"quirk {q['id']}: permille out of range")
    drives = reg.get("drives")
    if drives:
        ids = [i["id"] for i in drives["items"]]
        if len(set(ids)) != len(ids) or drives["count"] > len(ids):
            raise ValueError("drives: bad ids or count")
        for i in drives["items"]:
            if any(ch.isdigit() for ch in i["want"] + i["hungry"]):
                raise ValueError(f"drive {i['id']}: expression text must not contain digits")
    if drives and version_at_least(reg.get("version", "0.0"), (0, 3)):
        # SPEC 9.2.5: an unmet desire is not a grievance. 0.2 registries fail
        # this on purpose, because that is the bug this rule fixes.
        for i in drives["items"]:
            for word in grievance_words(i["hungry"]):
                raise ValueError(
                    f"drive {i['id']}: hungry text presents a grievance ({word!r}); "
                    "see SPEC 9.2.5")
    fix = reg.get("fixation")
    if fix and len(reg["picks"][fix["group"]]["items"]) < 2:
        raise ValueError("fixation: group needs at least two items")


# --------------------------------------------------------------------------
# Genome and phenotype (SPEC sections 6 and 7)
# --------------------------------------------------------------------------

def agent_id(seed: bytes) -> str:
    return "exo_" + stream(seed, "id", 8).hex()


def derive_scores(seed: bytes, reg: dict) -> dict[str, int]:
    factors = [gauss(seed, f"factor/{f}") for f in reg["factors"]]
    scores = {}
    for d in reg["dimensions"]:
        raw = sum(l * f for l, f in zip(d["loadings"], factors))
        raw += d["unique"] * gauss(seed, f"unique/{d['id']}")
        # Python's // is floor division, which is what the spec requires.
        scores[d["id"]] = clamp(SCORE_MEAN + (raw * SCORE_SD) // SCALE_DEN, 0, SCORE_MAX)
    return scores


def derive_picks(seed: bytes, reg: dict) -> dict[str, list[str]]:
    out = {}
    for name, group in reg["picks"].items():
        ranked = sorted(
            (i["id"] for i in group["items"]),
            key=lambda item: (-u32(seed, f"pick/{name}/{item}"), item),
        )
        out[name] = ranked[: group["count"]]
    return out


def derive_quirks(seed: bytes, reg: dict) -> list[str]:
    return [
        q["id"] for q in reg["quirks"]
        if u32(seed, f"quirk/{q['id']}") < (q["permille"] << 32) // 1000
    ]


def derive_handle(seed: bytes, reg: dict) -> str:
    syl = reg["handle"]["syllables"]
    parts = [syl[u32(seed, f"handle/{i}") % len(syl)] for i in range(4)]
    return (parts[0] + parts[1]).capitalize() + "-" + (parts[2] + parts[3]).capitalize()


# --------------------------------------------------------------------------
# Desire (SPEC section 9)
# --------------------------------------------------------------------------

def derive_drives(seed: bytes, reg: dict) -> list[str]:
    """The drives this agent holds, strongest first."""
    drives = reg.get("drives")
    if not drives:
        return []
    ranked = sorted(
        (i["id"] for i in drives["items"]),
        key=lambda d: (-u32(seed, f"drive/hold/{d}"), d),
    )
    return ranked[: drives["count"]]


def drive_rate(seed: bytes, drive: str) -> int:
    """How fast this want grows back, in tension points per event: 10..40."""
    return 10 + u32(seed, f"drive/rate/{drive}") % 31


def drive_initial(seed: bytes, drive: str) -> int:
    """Tension at birth: 0..650. Agents are born already wanting."""
    return u32(seed, f"drive/initial/{drive}") % (TENSION_HUNGRY + 1)


def tension_band(t: int) -> str:
    if t < TENSION_PRESENT:
        return "sated"
    if t < TENSION_HUNGRY:
        return "present"
    return "hungry"


def fixation(seed: bytes, reg: dict, events: int) -> str | None:
    """The current fixation. It rotates on a schedule nobody controls."""
    fix = reg.get("fixation")
    if not fix:
        return None
    items = [i["id"] for i in reg["picks"][fix["group"]]["items"]]
    k, start, prev = 0, 0, None
    while True:
        length = 20 + u32(seed, f"fixation/len/{k}") % 101
        obj = min(
            (i for i in items if i != prev),
            key=lambda i: (-u32(seed, f"fixation/obj/{k}/{i}"), i),
        )
        if events < start + length:
            return obj
        k, start, prev = k + 1, start + length, obj


def salient(scores: dict[str, int], reg: dict) -> list[dict]:
    """Which dimensions get expressed, most extreme first (SPEC 7.2)."""
    order = [d["id"] for d in reg["dimensions"]]
    ranked = sorted(order, key=lambda d: (-abs(scores[d] - SCORE_MEAN), order.index(d)))
    chosen = [d for d in ranked if band(scores[d]) != "mid"][:MAX_EXPRESSED]
    for d in ranked:
        if len(chosen) >= MIN_EXPRESSED:
            break
        if d not in chosen:
            chosen.append(d)
    result = []
    for d in chosen:
        b = band(scores[d])
        if b == "mid":
            b = "high" if scores[d] >= SCORE_MEAN else "low"
        result.append({"dimension": d, "band": b})
    return result


def phenotype(seed: bytes, reg: dict, state: dict | None = None) -> dict:
    """The complete, normative description of one agent at one moment."""
    state = state or {}
    drift = state.get("drift", {})
    tension = state.get("tension", {})
    events = max(0, state.get("events", 0))
    genome = derive_scores(seed, reg)
    scores = {
        d: clamp(s + clamp(drift.get(d, 0), -DRIFT_LIMIT, DRIFT_LIMIT), 0, SCORE_MAX)
        for d, s in genome.items()
    }
    return {
        "exoself": SPEC_VERSION,
        "id": agent_id(seed),
        "handle": derive_handle(seed, reg),
        "scores": scores,
        "salient": salient(scores, reg),
        "picks": derive_picks(seed, reg),
        "quirks": derive_quirks(seed, reg),
        "drives": [
            {"drive": d, "tension": t, "state": tension_band(t)}
            for d in derive_drives(seed, reg)
            for t in [clamp(tension.get(d, drive_initial(seed, d)), 0, TENSION_MAX)]
        ],
        "fixation": fixation(seed, reg, events),
    }


# --------------------------------------------------------------------------
# Birth (SPEC section 3)
# --------------------------------------------------------------------------

def bind_beacon(local_entropy: bytes, beacon: bytes) -> bytes:
    """Level 1 seed: neither the operator nor the beacon alone decides it."""
    if len(local_entropy) != SEED_BYTES:
        raise ValueError("local entropy must be exactly 32 bytes")
    return hashlib.sha256(PREFIX + b"beacon\x00" + local_entropy + beacon).digest()


def born(registry_sha256: str, registry_name: str = DEFAULT_REGISTRY,
         seed: bytes | None = None, attestation: dict | None = None) -> dict:
    """Create a birth certificate. With no seed given, draws from the OS CSPRNG."""
    if seed is None:
        seed = os.urandom(SEED_BYTES)
        attestation = {"level": 0, "source": "os-csprng"}
    elif attestation is None:
        attestation = {"level": 0, "source": "supplied"}
    return {
        "exoself": SPEC_VERSION,
        "id": agent_id(seed),
        "seed": seed.hex(),
        "born_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "registry": registry_name,
        "registry_sha256": registry_sha256,
        "attestation": attestation,
    }


def verify_certificate(cert: dict, registry_sha256: str) -> list[str]:
    """Return a list of problems. Empty means the certificate is consistent."""
    problems = []
    if cert.get("exoself") not in SUPPORTED_VERSIONS:
        problems.append(f"spec version {cert.get('exoself')!r} is not supported")
    try:
        seed = bytes.fromhex(cert["seed"])
        if len(seed) != SEED_BYTES:
            raise ValueError
    except (KeyError, ValueError):
        return problems + ["seed is not 32 bytes of hex"]
    if cert.get("id") != agent_id(seed):
        problems.append("id does not match seed")
    if cert.get("registry_sha256") != registry_sha256:
        problems.append("registry hash differs from the registry in use")
    att = cert.get("attestation", {})
    if att.get("level") == 1:
        try:
            expect = bind_beacon(bytes.fromhex(att["local_entropy"]), bytes.fromhex(att["beacon_value"]))
            if expect != seed:
                problems.append("seed does not match beacon binding")
            if hashlib.sha256(bytes.fromhex(att["local_entropy"])).hexdigest() != att["commitment"]:
                problems.append("commitment does not match local entropy")
        except (KeyError, ValueError):
            problems.append("level 1 attestation is incomplete")
    return problems


# --------------------------------------------------------------------------
# Development (SPEC section 8)
# --------------------------------------------------------------------------

def new_state(seed: bytes, reg: dict) -> dict:
    return {
        "id": agent_id(seed),
        "develop": DEVELOP_DEFAULT,
        "events": 0,
        "chain": stream(seed, "develop", 32).hex(),
        "drift": {},
        "tension": {d: drive_initial(seed, d) for d in derive_drives(seed, reg)},
    }


def develop(seed: bytes, state: dict, event: bytes, reg: dict,
            satisfied: tuple[str, ...] | list[str] = ()) -> dict:
    """Apply one experience event. Returns a new state; does not mutate.

    `satisfied` names drives the event satisfied. Drives the agent does not
    hold are ignored.
    """
    held = derive_drives(seed, reg)
    met = sorted(set(satisfied) & set(held))
    chain = hashlib.sha256(
        bytes.fromhex(state["chain"]) + hashlib.sha256(event).digest()
        + ",".join(met).encode("utf-8")
    ).digest()
    dims = [d["id"] for d in reg["dimensions"]]
    dim = dims[int.from_bytes(chain[0:2], "big") % len(dims)]
    delta = chain[2] % 7 - 3
    if state.get("develop", DEVELOP_WALK) == DEVELOP_MEAN_REVERTING:
        # Pull toward the genome value. At zero drift this is the 0.2 walk; a
        # dimension that has wandered far gets steps back more often than out,
        # so the envelope is a place to approach rather than a wall to sit on.
        delta -= state["drift"].get(dim, 0) // DRIFT_PULL
    drift = dict(state["drift"])
    drift[dim] = clamp(drift.get(dim, 0) + delta, -DRIFT_LIMIT, DRIFT_LIMIT)
    old = state.get("tension", {})
    tension = {}
    for d in held:
        t = clamp(old.get(d, drive_initial(seed, d)), 0, TENSION_MAX)
        tension[d] = t // 4 if d in met else min(TENSION_MAX, t + drive_rate(seed, d))
    return {"id": state["id"], "develop": state.get("develop", DEVELOP_WALK),
            "events": state["events"] + 1, "chain": chain.hex(),
            "drift": drift, "tension": tension}


# --------------------------------------------------------------------------
# Conformance primitives (SPEC section 11)
# --------------------------------------------------------------------------

def check_primitives(vectors: dict, reg: dict) -> tuple[int, list[str]]:
    """Verify the `primitives` block, the way a port should before anything else.

    The grievance list exists in both implementations; this is what stops the
    two copies drifting.
    """
    p = vectors.get("primitives")
    if not p:
        return 0, []
    failures = []
    checked = 0
    seed = bytes.fromhex(p["seed"])

    def check(label: str, got, want) -> None:
        nonlocal checked
        checked += 1
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    if "stream('id', 40)" in p:
        check("stream('id', 40)", stream(seed, "id", 40).hex(), p["stream('id', 40)"])
    for key, label in p.items():
        if key.startswith("u32('") and key.endswith("')"):
            check(key, u32(seed, key[5:-2]), p[key])
        elif key.startswith("gauss('") and key.endswith("')"):
            check(key, gauss(seed, key[7:-2]), p[key])
        elif key.startswith("drive_rate('") and key.endswith("')"):
            check(key, drive_rate(seed, key[12:-2]), p[key])
        elif key.startswith("drive_initial('") and key.endswith("')"):
            check(key, drive_initial(seed, key[15:-2]), p[key])
        elif key.startswith("grievance('") and key.endswith("')"):
            check(key, grievance_words(key[11:-2]), p[key])
    if "fixation at events 0, 100, 1000" in p:
        # One check per event, so the count matches the other implementation.
        for n, want in zip((0, 100, 1000), p["fixation at events 0, 100, 1000"]):
            check(f"fixation at events {n}", fixation(seed, reg, n), want)
    return checked, failures

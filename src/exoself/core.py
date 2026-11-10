"""Reference implementation of the exoself derivation (SPEC.md, sections 3 to 7).

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

SPEC_VERSION = "0.1"
PREFIX = b"exoself/0.1/"
SEED_BYTES = 32

GAUSS_DRAWS = 12
GAUSS_CENTER = 393210  # 12 * 65535 / 2
SCALE_DEN = 6553600  # 65536 (sd of one gaussian) * 100 (loading unit)
SCORE_MEAN = 500
SCORE_SD = 150
SCORE_MAX = 1000

DRIFT_LIMIT = 100
MIN_EXPRESSED = 2
MAX_EXPRESSED = 5


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

def load_registry(path: str | None = None) -> tuple[dict, str]:
    """Return (registry, sha256 hex of the exact file bytes)."""
    if path is None:
        raw = (resources.files("exoself") / "registry" / "core-0.1.json").read_bytes()
    else:
        with open(path, "rb") as f:
            raw = f.read()
    registry = json.loads(raw)
    validate_registry(registry)
    return registry, hashlib.sha256(raw).hexdigest()


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


def phenotype(seed: bytes, reg: dict, drift: dict[str, int] | None = None) -> dict:
    """The complete, normative description of one agent's temperament."""
    drift = drift or {}
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
    }


# --------------------------------------------------------------------------
# Birth (SPEC section 3)
# --------------------------------------------------------------------------

def bind_beacon(local_entropy: bytes, beacon: bytes) -> bytes:
    """Level 1 seed: neither the operator nor the beacon alone decides it."""
    if len(local_entropy) != SEED_BYTES:
        raise ValueError("local entropy must be exactly 32 bytes")
    return hashlib.sha256(PREFIX + b"beacon\x00" + local_entropy + beacon).digest()


def born(registry_sha256: str, registry_name: str = "core@0.1",
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
    if cert.get("exoself") != SPEC_VERSION:
        problems.append(f"spec version {cert.get('exoself')!r}, expected {SPEC_VERSION!r}")
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

def new_state(seed: bytes) -> dict:
    return {"id": agent_id(seed), "events": 0, "chain": stream(seed, "develop", 32).hex(), "drift": {}}


def develop(state: dict, event: bytes, reg: dict) -> dict:
    """Apply one experience event. Returns a new state; does not mutate."""
    chain = hashlib.sha256(bytes.fromhex(state["chain"]) + hashlib.sha256(event).digest()).digest()
    dims = [d["id"] for d in reg["dimensions"]]
    dim = dims[int.from_bytes(chain[0:2], "big") % len(dims)]
    delta = chain[2] % 7 - 3
    drift = dict(state["drift"])
    drift[dim] = clamp(drift.get(dim, 0) + delta, -DRIFT_LIMIT, DRIFT_LIMIT)
    return {"id": state["id"], "events": state["events"] + 1, "chain": chain.hex(), "drift": drift}

import hashlib
import json
import pathlib
import statistics

import pytest

from exoself import core, render
from exoself.cli import main

ROOT = pathlib.Path(__file__).resolve().parent.parent
REG, REG_HASH = core.load_registry()


def seeds(n):
    return [hashlib.sha256(b"test/%d" % i).digest() for i in range(n)]


def test_conformance_vectors():
    vectors = json.loads((ROOT / "spec" / "vectors.json").read_text())
    assert vectors["registry_sha256"] == REG_HASH
    assert len(vectors["vectors"]) >= 8
    for v in vectors["vectors"]:
        assert core.phenotype(bytes.fromhex(v["seed"]), REG, v.get("drift")) == v["phenotype"]


def test_deterministic_and_seed_sensitive():
    a, b = seeds(2)
    assert core.phenotype(a, REG) == core.phenotype(a, REG)
    assert core.phenotype(a, REG) != core.phenotype(b, REG)


def test_population_statistics():
    pop = [core.derive_scores(s, REG) for s in seeds(4000)]
    for d in REG["dimensions"]:
        xs = [p[d["id"]] for p in pop]
        assert abs(statistics.mean(xs) - 500) < 10, d["id"]
        assert abs(statistics.pstdev(xs) - 150) < 10, d["id"]
        assert 0 <= min(xs) and max(xs) <= 1000
    # Shared factors must produce the intended sign of correlation.
    r = statistics.correlation
    col = lambda k: [p[k] for p in pop]
    assert r(col("directness"), col("assertiveness")) > 0.25
    assert r(col("orderliness"), col("novelty")) < -0.15
    assert abs(r(col("warmth"), col("abstraction"))) < 0.06


def test_salient_bounds():
    for s in seeds(500):
        p = core.phenotype(s, REG)
        assert core.MIN_EXPRESSED <= len(p["salient"]) <= core.MAX_EXPRESSED
        assert all(x["band"] != "mid" for x in p["salient"])
        assert len(p["picks"]["interests"]) == 3 and len(set(p["picks"]["interests"])) == 3


def test_quirk_rate():
    n = 3000
    hits = sum(len(core.derive_quirks(s, REG)) for s in seeds(n))
    expected = sum(q["permille"] for q in REG["quirks"]) / 1000
    assert abs(hits / n - expected) < 0.15


def test_registry_extension_is_stable():
    """Adding a quirk or an unrelated dimension must not disturb existing traits."""
    ext = json.loads(json.dumps(REG))
    ext["quirks"].append({"id": "new-quirk", "permille": 500, "text": "x"})
    ext["dimensions"].append({
        "id": "extra", "poles": ["a", "b"], "loadings": [0, 0, 0, 0], "unique": 100,
        "text": {b: "x" for b in ("very_low", "low", "high", "very_high")},
    })
    core.validate_registry(ext)
    for s in seeds(50):
        base, new = core.phenotype(s, REG), core.phenotype(s, ext)
        assert all(new["scores"][k] == v for k, v in base["scores"].items())
        assert [q for q in new["quirks"] if q != "new-quirk"] == base["quirks"]
        assert new["picks"] == base["picks"]


def test_rendering_rules():
    for s in seeds(200):
        p = core.phenotype(s, REG)
        for fn in render.RENDERERS.values():
            text = fn(p, REG)
            body = text.replace(p["id"], "").replace(p["exoself"], "")
            assert not any(ch.isdigit() for ch in body), "numbers leaked into expression"
            assert render.PRECEDENCE in text and render.RESTRAINT in text
            assert "—" not in text


def test_registry_validation_rejects_bad_loadings():
    bad = json.loads(json.dumps(REG))
    bad["dimensions"][0]["unique"] = 10
    with pytest.raises(ValueError):
        core.validate_registry(bad)


def test_development_is_bounded_and_chained():
    seed = seeds(1)[0]
    state = core.new_state(seed)
    for i in range(5000):
        state = core.develop(state, b"event %d" % i, REG)
    assert state["events"] == 5000
    assert all(abs(v) <= core.DRIFT_LIMIT for v in state["drift"].values())
    # Same events in a different order give a different chain.
    a = core.develop(core.develop(core.new_state(seed), b"x", REG), b"y", REG)
    b = core.develop(core.develop(core.new_state(seed), b"y", REG), b"x", REG)
    assert a["chain"] != b["chain"]
    # Drift moves the phenotype but never past the envelope.
    g, p = core.phenotype(seed, REG), core.phenotype(seed, REG, state["drift"])
    assert all(abs(p["scores"][k] - g["scores"][k]) <= core.DRIFT_LIMIT for k in g["scores"])
    # Hostile drift values are clamped.
    wild = core.phenotype(seed, REG, {"warmth": 9999})
    assert wild["scores"]["warmth"] - g["scores"]["warmth"] <= core.DRIFT_LIMIT


def test_certificate_roundtrip_and_tamper():
    cert = core.born(REG_HASH)
    assert core.verify_certificate(cert, REG_HASH) == []
    assert core.verify_certificate({**cert, "id": "exo_0000000000000000"}, REG_HASH)
    assert core.verify_certificate(cert, "0" * 64)
    assert core.verify_certificate({**cert, "seed": "abcd"}, REG_HASH)


def test_beacon_binding():
    local, beacon = b"\x01" * 32, b"\x02" * 48
    seed = core.bind_beacon(local, beacon)
    att = {"level": 1, "commitment": hashlib.sha256(local).hexdigest(),
           "local_entropy": local.hex(), "beacon_value": beacon.hex(), "beacon": "test"}
    cert = core.born(REG_HASH, seed=seed, attestation=att)
    assert core.verify_certificate(cert, REG_HASH) == []
    att_bad = {**att, "beacon_value": (b"\x03" * 48).hex()}
    assert core.verify_certificate({**cert, "attestation": att_bad}, REG_HASH)


def test_cli_born_refuses_reroll(tmp_path, capsys):
    out = str(tmp_path / "birth.json")
    assert main(["born", "-o", out]) == 0
    first = pathlib.Path(out).read_text()
    assert main(["born", "-o", out]) == 1
    assert pathlib.Path(out).read_text() == first
    assert main(["verify", out]) == 0
    assert main(["show", out, "--format", "soul"]) == 0
    state = str(tmp_path / "state.json")
    assert main(["develop", out, state, "--event", "first session"]) == 0
    assert main(["show", out, "--state", state, "--format", "json"]) == 0
    assert main(["conformance", str(ROOT / "spec" / "vectors.json")]) == 0

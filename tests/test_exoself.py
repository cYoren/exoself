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
        assert core.phenotype(bytes.fromhex(v["seed"]), REG, v.get("state")) == v["phenotype"]


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
            quiet = fn(p, REG)
            assert render.APPETITE not in quiet and "Lately you are taken" not in quiet
            text = fn(p, REG, desire=True)
            body = text.replace(p["id"], "").replace(p["exoself"], "")
            assert not any(ch.isdigit() for ch in body), "numbers leaked into expression"
            assert render.PRECEDENCE in text and render.RESTRAINT in text
            assert render.APPETITE in text
            assert "—" not in text


def test_site_artifacts_match_their_sources():
    """docs/vectors.json is committed for GitHub Pages; catch it drifting."""
    assert (ROOT / "docs" / "vectors.json").read_bytes() == (ROOT / "spec" / "vectors.json").read_bytes()
    wasm = ROOT / "docs" / "exoself_wasm.wasm"
    assert wasm.is_file() and wasm.stat().st_size > 10_000
    assert wasm.read_bytes()[:4] == b"\0asm"


def test_registry_validation_rejects_bad_loadings():
    bad = json.loads(json.dumps(REG))
    bad["dimensions"][0]["unique"] = 10
    with pytest.raises(ValueError):
        core.validate_registry(bad)


def test_development_is_bounded_and_chained():
    seed = seeds(1)[0]
    state = core.new_state(seed, REG)
    for i in range(5000):
        state = core.develop(seed, state, b"event %d" % i, REG)
    assert state["events"] == 5000
    assert all(abs(v) <= core.DRIFT_LIMIT for v in state["drift"].values())
    # Same events in a different order give a different chain.
    new = core.new_state(seed, REG)
    a = core.develop(seed, core.develop(seed, new, b"x", REG), b"y", REG)
    b = core.develop(seed, core.develop(seed, new, b"y", REG), b"x", REG)
    assert a["chain"] != b["chain"]
    # Drift moves the phenotype but never past the envelope.
    g, p = core.phenotype(seed, REG), core.phenotype(seed, REG, state)
    assert all(abs(p["scores"][k] - g["scores"][k]) <= core.DRIFT_LIMIT for k in g["scores"])
    # Hostile drift values are clamped.
    wild = core.phenotype(seed, REG, {"drift": {"warmth": 9999}})
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
    assert main(["born", "-o", out]) == 2      # exit 2: operational error, as Rust
    assert pathlib.Path(out).read_text() == first
    assert main(["verify", out]) == 0
    assert main(["show", out, "--format", "soul"]) == 0
    assert "## Wants" not in capsys.readouterr().out
    assert main(["show", out, "--format", "soul", "--desire"]) == 0
    assert "## Wants" in capsys.readouterr().out
    state = str(tmp_path / "state.json")
    assert main(["develop", out, state, "--event", "first session"]) == 0
    held = json.loads(pathlib.Path(state).read_text())["tension"]
    assert main(["develop", out, state, "--event", "s", "--satisfied", next(iter(held))]) == 0
    assert main(["develop", out, state, "--event", "s", "--satisfied", "power"]) == 2
    assert main(["show", out, "--state", state, "--format", "json"]) == 0
    assert main(["conformance", str(ROOT / "spec" / "vectors.json")]) == 0


# ---------------------------------------------------------------- desire

def test_registry_upgrade_keeps_everyone_who_they_were():
    """0.2 added desire, 0.3 added a voice. Neither may move a living agent."""
    old, _ = core.load_registry(str(ROOT / "src/exoself/registry/core-0.1.json"))
    prev, _ = core.load_registry(str(ROOT / "src/exoself/registry/core-0.2.json"))
    for s in seeds(100):
        a, b = core.phenotype(s, old), core.phenotype(s, REG)
        for k in ("id", "handle", "scores", "salient", "quirks"):
            assert a[k] == b[k]
        assert a["drives"] == [] and a["fixation"] is None
        assert "Wants" not in render.render_soul(a, old, desire=True)
        # 0.2 to 0.3: every group that existed keeps every pick, and the new
        # groups are additive.
        c = core.phenotype(s, prev)
        assert c["scores"] == b["scores"] and c["salient"] == b["salient"]
        for group in prev["picks"]:
            assert c["picks"][group] == b["picks"][group]
        assert set(b["picks"]) - set(c["picks"]) == {"rhythm", "hedging", "examples", "formality"}


def test_the_voice_layer_speaks_on_every_seed():
    """A register group has one option per axis, so it always says something."""
    for s in seeds(200):
        p = core.phenotype(s, REG)
        for axis in ("rhythm", "hedging", "examples", "formality"):
            assert len(p["picks"][axis]) == 1
        text = render.render_prompt(p, REG)
        for axis in ("rhythm", "hedging", "examples", "formality"):
            item = p["picks"][axis][0]
            want = next(i["text"] for i in REG["picks"][axis]["items"] if i["id"] == item)
            assert want in text


def test_an_unmet_drive_is_not_a_grievance():
    """SPEC 9.2.5, enforced at load time rather than trusted."""
    assert core.grievance_words("Your sentences are short.") == []
    assert core.grievance_words("It has been a while.") == ["has been a while"]
    assert core.grievance_words("something owed, in a while") == ["in a while"]
    for item in REG["drives"]["items"]:
        assert core.grievance_words(item["hungry"]) == [], item["id"]
        # The hungry text sharpens the want; it does not describe a deficit.
        assert item["hungry"] != item["want"]
    # The 0.2 registry fails that rule, which is the bug it fixes. Relabelled as
    # 0.3 it must be refused rather than quietly accepted.
    old = json.loads((ROOT / "src/exoself/registry/core-0.2.json").read_text())
    old["version"] = "0.3"
    with pytest.raises(ValueError, match="grievance"):
        core.validate_registry(old)


def test_development_pulls_back_instead_of_saturating():
    """SPEC 8.3: the envelope is approached, never sat on."""
    def worst(mode: str, events: int) -> int:
        top = 0
        for s in seeds(24):
            state = core.new_state(s, REG)
            state["develop"] = mode
            for i in range(events):
                state = core.develop(s, state, b"event %d" % i, REG)
            top = max(top, max((abs(v) for v in state["drift"].values()), default=0))
        return top

    assert worst(core.DEVELOP_WALK, 3000) > 60, "the old walk should saturate"
    assert worst(core.DEVELOP_MEAN_REVERTING, 3000) < 60
    assert worst(core.DEVELOP_MEAN_REVERTING, 20000) < 60


def test_an_old_state_file_still_replays():
    """A state written by 0.2 has no mode field, so it must replay as it did."""
    s = seeds(1)[0]
    walk = core.new_state(s, REG)
    walk["develop"] = core.DEVELOP_WALK
    legacy = {k: v for k, v in walk.items() if k != "develop"}
    assert "develop" not in legacy
    a = core.develop(s, legacy, b"thing", REG)
    b = core.develop(s, walk, b"thing", REG)
    assert a["chain"] == b["chain"] and a["drift"] == b["drift"]
    assert a["develop"] == core.DEVELOP_WALK


def test_drives_are_held_for_life():
    seed = seeds(1)[0]
    held = core.derive_drives(seed, REG)
    assert len(held) == REG["drives"]["count"] == len(set(held))
    state = core.new_state(seed, REG)
    for i in range(300):
        state = core.develop(seed, state, b"%d" % i, REG, held[:1] if i % 5 == 0 else [])
    assert [d["drive"] for d in core.phenotype(seed, REG, state)["drives"]] == held
    counts = {}
    for s in seeds(3000):
        for d in core.derive_drives(s, REG):
            counts[d] = counts.get(d, 0) + 1
    assert len(counts) == len(REG["drives"]["items"])
    assert max(counts.values()) < 1.25 * min(counts.values())


def test_tension_grows_with_deprivation_and_falls_with_satisfaction():
    seed = seeds(1)[0]
    held = core.derive_drives(seed, REG)
    state = core.new_state(seed, REG)
    assert all(0 <= t <= core.TENSION_HUNGRY for t in state["tension"].values())
    for i in range(200):
        prev = state
        state = core.develop(seed, state, b"%d" % i, REG)
        assert all(state["tension"][d] >= prev["tension"][d] for d in held)
    assert all(t == core.TENSION_MAX for t in state["tension"].values())
    assert all(d["state"] == "hungry" for d in core.phenotype(seed, REG, state)["drives"])
    fed = core.develop(seed, state, b"x", REG, [held[0]])
    assert fed["tension"][held[0]] == core.TENSION_MAX // 4
    assert fed["tension"][held[1]] == core.TENSION_MAX
    # Satisfaction is part of history: it changes the chain.
    assert fed["chain"] != core.develop(seed, state, b"x", REG)["chain"]
    # A drive the agent does not hold cannot be fed, and leaves no trace.
    other = next(i["id"] for i in REG["drives"]["items"] if i["id"] not in held)
    assert core.develop(seed, state, b"x", REG, [other]) == core.develop(seed, state, b"x", REG)
    # Hostile state is clamped.
    wild = core.phenotype(seed, REG, {"tension": {held[0]: 10**9, held[1]: -5}})
    assert [d["tension"] for d in wild["drives"]][:2] == [core.TENSION_MAX, 0]


def test_wants_are_silent_when_sated():
    seed = seeds(1)[0]
    held = core.derive_drives(seed, REG)
    drives = {i["id"]: i for i in REG["drives"]["items"]}
    sated = core.phenotype(seed, REG, {"tension": {d: 0 for d in held}})
    hungry = core.phenotype(seed, REG, {"tension": {d: 1000 for d in held}})
    for d in held:
        assert drives[d]["want"] not in render.render_prompt(sated, REG, desire=True)
        assert drives[d]["hungry"] not in render.render_prompt(sated, REG, desire=True)
        assert drives[d]["hungry"] in render.render_prompt(hungry, REG, desire=True)
        # Off by default: even a starving agent says nothing unless enabled.
        assert drives[d]["hungry"] not in render.render_prompt(hungry, REG)
        assert drives[d]["hungry"] not in render.render_soul(hungry, REG)


def test_fixation_rotates_and_never_repeats_back_to_back():
    interests = {i["id"] for i in REG["picks"]["interests"]["items"]}
    for seed in seeds(4):
        seq = [core.fixation(seed, REG, n) for n in range(0, 3000)]
        assert set(seq) <= interests
        runs = [seq[0]] + [b for a, b in zip(seq, seq[1:]) if a != b]
        assert len(runs) >= 3000 // 120
        assert all(a != b for a, b in zip(runs, runs[1:]))
        lengths = [len([1 for x in seq[:1500] if x == r]) for r in set(runs[:5])]
        assert min(lengths) >= 20


def test_cli_honours_the_pinned_registry(tmp_path, capsys):
    """A certificate born under core@0.1 keeps working, and can be moved up on purpose."""
    old, old_hash = core.load_registry(name="core@0.1")
    cert = core.born(old_hash, "core@0.1", seed=seeds(1)[0])
    cert["exoself"] = "0.1"
    path = tmp_path / "old.json"
    path.write_text(json.dumps(cert))
    assert main(["verify", str(path)]) == 0
    capsys.readouterr()
    assert main(["show", str(path), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["drives"] == []
    new = str(ROOT / "src/exoself/registry/core-0.2.json")
    assert main(["show", str(path), "--registry", new, "--format", "json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["drives"]) == 3
    path.write_text(json.dumps({**cert, "registry": "../../etc/passwd"}))
    assert main(["show", str(path)]) == 2


# ---------------------------------------------------------------- registry edge cases

def test_a_registry_without_drives_is_valid():
    """SPEC 5.1 makes drives and fixation optional. A 0.3 registry that has
    neither must load; the grievance check must not run on a missing list."""
    bare = {
        "registry": "bare",
        "version": "0.3",
        "factors": ["f"],
        "dimensions": [{
            "id": "only",
            "poles": ["low", "high"],
            "loadings": [100],
            "unique": 0,
            "text": {b: "You are plain." for b in ("very_low", "low", "high", "very_high")},
        }],
        "picks": {},
        "quirks": [],
        "handle": {"syllables": ["ka"]},
    }
    core.validate_registry(bare)
    seed = seeds(1)[0]
    ph = core.phenotype(seed, bare)
    assert ph["drives"] == [] and ph["fixation"] is None
    assert ph["picks"] == {}


def test_registry_versions_compare_numerically():
    """0.10 is later than 0.3. A string comparison would switch the 9.2.5
    grievance check off at the tenth minor version without anyone noticing."""
    assert core.version_at_least("0.10", (0, 3))
    assert core.version_at_least("0.3", (0, 3))
    assert core.version_at_least("1.0", (0, 3))
    assert not core.version_at_least("0.2", (0, 3))
    assert not core.version_at_least("bogus", (0, 3))


def test_a_satisfied_drive_is_named_once_per_flag(tmp_path):
    """Rust takes `--satisfied DRIVE` repeatedly. Python used to take a list,
    so the same command line meant two different histories."""
    out = str(tmp_path / "birth.json")
    state = str(tmp_path / "state.json")
    assert main(["born", "-o", out]) == 0
    held = list(core.derive_drives(bytes.fromhex(json.loads(pathlib.Path(out).read_text())["seed"]), REG))
    assert len(held) >= 2
    assert main(["develop", out, state, "--event", "e",
                 "--satisfied", held[0], "--satisfied", held[1]]) == 0
    seed = bytes.fromhex(json.loads(pathlib.Path(out).read_text())["seed"])
    tension = json.loads(pathlib.Path(state).read_text())["tension"]
    # Satisfaction quarters the tension; exact, so a drive born at 0..3 does
    # not make this test flaky.
    for drive in held[:2]:
        assert tension[drive] == core.drive_initial(seed, drive) // 4
    with pytest.raises(SystemExit):
        main(["develop", out, state, "--event", "e", "--satisfied", held[0], held[1]])


# ---------------------------------------------------------------- state locking

def test_the_state_lock_is_taken_and_released(tmp_path):
    """Two hosts can record sessions for one agent. Development is
    read-modify-write on a chained file, so the write is serialised."""
    from exoself.cli import state_lock
    state = str(tmp_path / "state.json")
    lock = pathlib.Path(state + ".lock")
    with state_lock(state):
        assert lock.exists()
    assert not lock.exists()


def test_a_lock_left_by_a_dead_process_is_stolen(tmp_path):
    import os
    import time
    from exoself.cli import state_lock
    state = str(tmp_path / "state.json")
    lock = pathlib.Path(state + ".lock")
    lock.write_text("999999\n")
    old = time.time() - 120
    os.utime(lock, (old, old))
    with state_lock(state):
        assert lock.exists()
    assert not lock.exists()


# ---------------------------------------------------------------- identity

def test_a_state_file_from_another_agent_is_refused(tmp_path, capsys):
    """Drift belongs to one agent. Rendering A with B's state would show a
    phenotype that no seed produces."""
    a = str(tmp_path / "a.json")
    b = str(tmp_path / "b.json")
    state = str(tmp_path / "state.json")
    assert main(["born", "-o", a]) == 0
    assert main(["born", "-o", b]) == 0
    assert main(["develop", a, state, "--event", "x"]) == 0
    assert main(["show", b, "--state", state]) == 2
    assert "different agent" in capsys.readouterr().err
    assert main(["show", a, "--state", state]) == 0


def test_forgetting_the_state_flag_says_so(tmp_path, capsys):
    cert = str(tmp_path / "birth.json")
    state = str(tmp_path / "state.json")
    assert main(["born", "-o", cert]) == 0
    assert main(["develop", cert, state, "--event", "x"]) == 0
    capsys.readouterr()
    assert main(["show", cert]) == 0
    assert "--state was not given" in capsys.readouterr().err
    assert main(["show", cert, "--state", state]) == 0
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------- full CLI surface

def test_the_python_cli_has_the_same_commands_as_the_rust_one():
    """`pip install exoself` must be a complete path: a host wired with the
    Python binary needs install and mcp, not just the derivation."""
    import argparse
    from exoself.cli import build_parser
    parser = build_parser()
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    names = set(actions[0].choices)
    assert names == {"born", "show", "develop", "verify", "status", "conformance",
                     "commit", "install", "mcp", "version"}, names


def test_install_writes_everything_a_host_needs(tmp_path):
    import json as _json
    d = str(tmp_path)
    assert main(["install", "--dir", d, "--json-config", ".mcp.json", "--desire"]) == 0
    for name in (".exoself/birth.json", ".exoself/state.json", "SOUL.md",
                 "AGENTS.md", ".mcp.json"):
        assert (tmp_path / name).exists(), name
    entry = _json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["exoself"]
    assert "--desire" in entry["args"]
    # Re-running must not draw a second seed.
    cert = (tmp_path / ".exoself/birth.json").read_text()
    assert main(["install", "--dir", d]) == 0
    assert (tmp_path / ".exoself/birth.json").read_text() == cert
    assert (tmp_path / "AGENTS.md").read_text().count("exoself:begin") == 1


def test_install_keeps_other_mcp_servers_and_user_text(tmp_path):
    import json as _json
    d = str(tmp_path)
    (tmp_path / ".mcp.json").write_text(_json.dumps(
        {"mcpServers": {"other": {"command": "node"}}, "theme": "dark"}))
    (tmp_path / "AGENTS.md").write_text("# Project\n\nRules.\n")
    assert main(["install", "--dir", d, "--json-config", ".mcp.json"]) == 0
    cfg = _json.loads((tmp_path / ".mcp.json").read_text())
    assert set(cfg["mcpServers"]) == {"other", "exoself"}
    assert cfg["theme"] == "dark"
    brief = (tmp_path / "AGENTS.md").read_text()
    assert brief.startswith("# Project\n\nRules.\n")
    assert brief.count("exoself:begin") == 1


def test_commit_writes_a_private_pre_image_and_refuses_to_replace_it(tmp_path):
    import os
    import stat
    out = str(tmp_path / "commitment.json")
    assert main(["commit", "-o", out]) == 0
    secret = tmp_path / "commitment.secret.json"
    assert secret.exists()
    if os.name == "posix":
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    body = json.loads(secret.read_text())
    assert hashlib.sha256(bytes.fromhex(body["local_entropy"])).hexdigest() == body["commitment"]
    assert main(["commit", "-o", out]) == 2      # never overwrite a commitment


def test_a_level_one_birth_binds_to_the_beacon(tmp_path):
    import os
    out = str(tmp_path / "commitment.json")
    assert main(["commit", "-o", out]) == 0
    entropy = json.loads((tmp_path / "commitment.secret.json").read_text())["local_entropy"]
    beacon = "ab" * 32
    cert = str(tmp_path / "birth.json")
    assert main(["born", "-o", cert, "--beacon", "drand:quicknet:1",
                 "--beacon-value", beacon, "--local-entropy", entropy]) == 0
    assert main(["verify", cert]) == 0
    assert json.loads(pathlib.Path(cert).read_text())["attestation"]["level"] == 1
    # A supplied seed without a beacon is not a draw; the path is closed.
    assert main(["born", "-o", str(tmp_path / "x.json"),
                 "--local-entropy", entropy]) == 2
    assert not (tmp_path / "x.json").exists()


def test_conformance_checks_the_primitives_too():
    """SPEC 11 asks for the primitives as well as the phenotypes. The Rust
    binary reported 25 checks and the Python one reported 14: same word,
    different amount of checking."""
    vectors = json.loads((ROOT / "spec" / "vectors.json").read_text())
    checked, failures = core.check_primitives(vectors, REG)
    assert failures == []
    assert checked == 11, checked
    assert checked + len(vectors["vectors"]) == 25


def test_the_site_does_not_offer_a_free_reroll():
    """Every other surface refuses to draw twice: `born` will not overwrite, and
    no MCP tool draws at all. The page must not be the exception."""
    js = (ROOT / "docs" / "app.js").read_text()
    assert "localStorage.setItem(STORE" in js, "the drawn seed must be kept"
    assert "btn.disabled = true" in js, "the draw button must be spent"
    assert "if (storedSeed()) return;" in js, "draw() must refuse a second draw"
    html = (ROOT / "docs" / "index.html").read_text()
    # One draw control, and no second one hiding in a link or a form.
    assert html.count('id="draw"') == 1


def test_the_crate_copies_match_their_sources():
    """`cargo publish` packages only the crate directory, so the registry and
    the vectors are copied inside it. A copy that drifts is a published crate
    that derives a different species from the one the repo tests."""
    crate = ROOT / "rust" / "crates" / "exoself-core"
    for name in ("core-0.1.json", "core-0.2.json", "core-0.3.json"):
        assert (crate / "registry" / name).read_bytes() == \
            (ROOT / "src" / "exoself" / "registry" / name).read_bytes(), name
    assert (crate / "spec" / "vectors.json").read_bytes() == \
        (ROOT / "spec" / "vectors.json").read_bytes()


# ---------------------------------------------------------------- the brief stays current

def test_a_session_refreshes_the_generated_files(tmp_path):
    """The brief and the persona are snapshots. If `develop` does not rewrite
    them, the most common kind of host reads the agent as it was born."""
    d = str(tmp_path)
    assert main(["install", "--dir", d, "--desire"]) == 0
    brief = tmp_path / "AGENTS.md"
    soul = tmp_path / "SOUL.md"
    assert "Sessions recorded: 0" in brief.read_text()
    for i in range(12):
        assert main(["develop", str(tmp_path / ".exoself/birth.json"),
                     str(tmp_path / ".exoself/state.json"),
                     "--event", f"session {i}"]) == 0
    assert "Sessions recorded: 12" in brief.read_text()
    # The operator's appetite choice survives the refresh.
    assert "These are appetites, not goals" in brief.read_text()
    assert "## Wants" in soul.read_text()
    # And the persona is exactly what a fresh render would produce.
    state = str(tmp_path / ".exoself/state.json")
    cert = str(tmp_path / ".exoself/birth.json")
    assert main(["show", cert, "--state", state, "--format", "soul", "--desire"]) == 0


def test_the_refresh_touches_only_our_own_files(tmp_path):
    d = str(tmp_path)
    (tmp_path / "NOTES.md").write_text("# my notes\n\nnot ours\n")
    (tmp_path / "CLAUDE.md").write_text("# project\n")
    assert main(["install", "--dir", d, "--file", "CLAUDE.md"]) == 0
    assert main(["develop", str(tmp_path / ".exoself/birth.json"),
                 str(tmp_path / ".exoself/state.json"), "--event", "x"]) == 0
    assert (tmp_path / "NOTES.md").read_text() == "# my notes\n\nnot ours\n"
    assert "Sessions recorded: 1" in (tmp_path / "CLAUDE.md").read_text()
    assert not (tmp_path / "AGENTS.md").exists()


def test_a_certificate_outside_an_installed_directory_writes_nothing(tmp_path):
    """`develop` on a loose certificate must not go looking around the tree."""
    cert = str(tmp_path / "birth.json")
    state = str(tmp_path / "state.json")
    (tmp_path / "AGENTS.md").write_text("# untouched\n")
    assert main(["born", "-o", cert]) == 0
    assert main(["develop", cert, state, "--event", "x"]) == 0
    assert (tmp_path / "AGENTS.md").read_text() == "# untouched\n"


def test_one_version_everywhere():
    """The version is written in three files. A release that disagrees with
    itself is the kind of thing nobody notices until the wheel is on PyPI."""
    import re
    from exoself.cli import VERSION
    pyproject = (ROOT / "pyproject.toml").read_text()
    cargo = (ROOT / "rust" / "Cargo.toml").read_text()
    py_version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    rs_version = re.search(r'^version = "([^"]+)"', cargo, re.M).group(1)
    assert VERSION == py_version == rs_version, (VERSION, py_version, rs_version)
    # And the MCP server reports the same one.
    from exoself import mcp
    assert mcp.VERSION == VERSION

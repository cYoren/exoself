"""Guards for the measurement tooling in tools/eval.py.

These are unit checks on the statistics and the aggregation, not on the model
run: nothing here calls an endpoint or overwrites a stored result. The point is
that a number in eval/REPORT.md can only move because the derivation moved,
not because a helper was edited and broke.
"""

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

spec = importlib.util.spec_from_file_location("eval_tools", ROOT / "tools" / "eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_seeds_are_reproducible_and_distinct():
    assert ev.seed_at(0) == ev.seed_at(0)
    assert ev.seed_at(0) != ev.seed_at(1)
    assert len(ev.seed_at(3)) == 32


def test_shingles_and_jaccard_bounds():
    a = ev.shingles("the quick brown fox")
    assert 0 < ev.jaccard(a, a) == 1.0
    assert ev.jaccard(a, ev.shingles("completely different words")) == 0.0
    assert ev.jaccard(set(), a) == 0.0
    # Case and whitespace must not matter.
    assert ev.shingles("The  Quick brown fox") == a


def test_binomial_two_sided_matches_intuition():
    # All successes at chance 0.5 cannot be a coincidence.
    assert ev.binom_two_sided(10, 10, 0.5) < 0.01
    # Exactly at chance is not significant.
    assert ev.binom_two_sided(5, 10, 0.5) > 0.9
    # A hit rate at chance for a rare event is also not significant.
    assert ev.binom_two_sided(5, 100, 0.0625) > 0.3
    # And a large lift over a rare event is.
    assert ev.binom_two_sided(37, 144, 0.0625) < 1e-9


def test_chao1_and_entropy_behave():
    assert ev.chao1({"a": 1}) == float("inf")
    assert ev.chao1({"a": 5, "b": 5}) == 2.0
    assert ev.chao1({"a": 1, "b": 1, "c": 2}) == pytest.approx(5.0)  # 3 + f1^2/(2 f2)
    assert ev.entropy_bits({"a": 1, "b": 1}) == pytest.approx(1.0)
    assert ev.entropy_bits({}) == 0.0


def test_aggregate_rolls_rows_up():
    rows = [
        {"context": 0, "task": 0, "intra_mean": 0.5, "inter_mean": 0.1,
         "vs_generic_mean": 0.1, "same_agent_nearest": 1.0},
        {"context": 0, "task": 1, "intra_mean": 0.5, "inter_mean": 0.1,
         "vs_generic_mean": 0.1, "same_agent_nearest": 0.0},
        {"context": 8, "task": 0, "intra_mean": 0.5, "inter_mean": 0.1,
         "vs_generic_mean": 0.1, "same_agent_nearest": 1.0},
        {"context": 8, "task": 1, "intra_mean": 0.5, "inter_mean": 0.1,
         "vs_generic_mean": 0.1, "same_agent_nearest": 0.0},
        {"context": 0, "task": 0, "kind": "pair", "correct": True},
        {"context": 0, "task": 0, "kind": "pair", "correct": False},
        {"context": 0, "task": 0, "kind": "many", "correct": True},
    ]
    agg = ev.aggregate(rows, agents=4, tasks=2, contexts=[0, 8])
    assert agg["nn_pooled"]["hits"] == 8  # 4 cells x 4 agents x a half
    assert agg["nn_pooled"]["trials"] == 16
    assert agg["nn_pooled"]["accuracy"] == 0.5
    assert agg["pair"]["hits"] == 1 and agg["pair"]["trials"] == 2
    assert agg["many"]["hits"] == 1 and agg["many"]["trials"] == 1
    assert set(agg["by_task"]) == {0, 1}
    assert agg["by_task"][0]["same_agent_nearest"] == 1.0
    assert agg["by_context"][8]["same_agent_nearest"] == 0.5
    # A p-value exists for every cell and is a probability.
    for cell in list(agg["by_task"].values()) + list(agg["by_context"].values()):
        assert 0.0 <= cell["nn_p"] <= 1.0


def test_stored_results_and_report_are_consistent():
    """The report must be rebuildable from what is committed."""
    report = ROOT / "eval" / "REPORT.md"
    assert report.is_file(), "run: python3 tools/eval.py report"
    text = report.read_text()
    results = ROOT / "eval" / "results"
    for name in ("population", "overlap", "stability", "behaviour"):
        p = results / f"{name}.json"
        assert p.is_file(), f"missing {p}"
        json.loads(p.read_text())
    beh = json.loads((results / "behaviour.json").read_text())
    assert str(beh["agents"]) in text
    # The claim the report makes about working-style tasks must still hold in
    # the stored rows, so nobody can soften the prose without the data.
    agg = ev.aggregate(beh["rows"], beh["agents"], beh["tasks"], beh["contexts"])
    assert agg["by_task"][2]["same_agent_nearest"] > 3 * (1 / beh["agents"])

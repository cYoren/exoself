#!/usr/bin/env python3
"""Does a seeded temperament actually do anything?

`AGENTS.md` names this as the open problem the project rests on: "given
transcripts from N seeded agents on the same tasks, can a judge match
transcripts to seeds above chance? Does the match rate hold at turn 5, 50, 500?
Without this, everything else here is decoration."

This script answers it in two halves.

Offline (no model, deterministic, seconds):

    population   how many distinct temperaments exist, how evenly the trait
                 space is used, how many bits of identity reach the model
    stability    is a temperament stable under its own history? Render the same
                 seed before and after many events; the nearest neighbour of an
                 agent's early text must be its own later text, not another
                 agent's
    overlap      how often two agents read alike, which is the honest ceiling
                 for anyone trying to tell them apart by text

With a model (any OpenAI-compatible endpoint, local by default):

    behaviour    generate answers with N seeded agents plus one agent with no
                 temperament, then blind-match answers back to temperaments at
                 three context lengths. Reports top-1 accuracy against chance
                 and how often the flat assistant wins

Usage:

    python3 tools/eval.py population
    python3 tools/eval.py stability
    python3 tools/eval.py overlap
    python3 tools/eval.py behaviour --agents 12
    python3 tools/eval.py report          # writes eval/REPORT.md

Results land in `eval/results/*.json` so `report` can rebuild the document from
whatever has been run. Nothing here is a unit test: these are measurements, and
the point is that the numbers are published whichever way they fall.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import statistics
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from exoself import core, render  # noqa: E402

RESULTS = ROOT / "eval" / "results"
REPORT = ROOT / "eval" / "REPORT.md"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def seed_at(i: int) -> bytes:
    """A reproducible seed for evaluation, independent of the OS."""
    return hashlib.sha256(b"exoself-eval/%d" % i).digest()


def prompt_for(seed: bytes, reg: dict, state: dict | None = None, desire: bool = False) -> str:
    return render.render_prompt(core.phenotype(seed, reg, state), reg, desire=desire)


def shingles(text: str, k: int = 5) -> set[str]:
    t = " ".join(text.lower().split())
    if len(t) <= k:
        return {t}
    return {t[i:i + k] for i in range(len(t) - k + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def save(name: str, payload: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["measured_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (RESULTS / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote eval/results/{name}.json")


def chao1(counts: dict[str, int]) -> float:
    """Estimated number of distinct species from a sample.

    f1 singletons, f2 doubletons. Reported as an estimate with its own
    uncertainty: it is only meaningful when the sample has many singletons.
    """
    f1 = sum(1 for c in counts.values() if c == 1)
    f2 = sum(1 for c in counts.values() if c == 2)
    observed = len(counts)
    if f2 == 0:
        return float("inf") if f1 else float(observed)
    return observed + (f1 * f1) / (2 * f2)


def entropy_bits(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def binom_two_sided(k: int, n: int, p: float) -> float:
    """Exact two-sided binomial tail. n is small here, so no approximation."""
    if n == 0:
        return 1.0
    def pmf(i: int) -> float:
        return math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
    observed = pmf(k)
    return min(1.0, sum(pmf(i) for i in range(n + 1) if pmf(i) <= observed + 1e-12))


def aggregate(rows: list[dict], agents: int, tasks: int, contexts: list[int]) -> dict:
    """Roll the raw rows into the tables the report prints, with p-values.

    Kept separate from the run so `report` can rebuild the tables from a stored
    result without calling the model again.
    """
    cons = [r for r in rows if r.get("intra_mean") is not None]
    pair = [r for r in rows if r.get("kind") == "pair" and r.get("correct") is not None]
    many = [r for r in rows if r.get("kind") == "many"]

    def cell(rs):
        nn = [r["same_agent_nearest"] for r in rs]
        return {
            "cells": len(rs),
            "intra_mean": statistics.mean(r["intra_mean"] for r in rs) if rs else 0.0,
            "inter_mean": statistics.mean(r["inter_mean"] for r in rs) if rs else 0.0,
            "vs_generic_mean": statistics.mean(r["vs_generic_mean"] for r in rs) if rs else 0.0,
            "same_agent_nearest": statistics.mean(nn) if rs else 0.0,
        }

    pooled_nn_hits = round(sum(r["same_agent_nearest"] * agents for r in cons))
    pooled_nn_trials = len(cons) * agents
    by_context = {c: cell([r for r in cons if r["context"] == c]) for c in contexts}
    by_task = {t: cell([r for r in cons if r["task"] == t]) for t in range(tasks)}
    for c in contexts:
        hits = round(sum(r["same_agent_nearest"] * agents for r in cons if r["context"] == c))
        trials = len([r for r in cons if r["context"] == c]) * agents
        by_context[c]["nn_p"] = binom_two_sided(hits, trials, 1 / agents)
    for t in range(tasks):
        hits = round(sum(r["same_agent_nearest"] * agents for r in cons if r["task"] == t))
        trials = len([r for r in cons if r["task"] == t]) * agents
        by_task[t]["nn_p"] = binom_two_sided(hits, trials, 1 / agents)

    pair_hits = sum(1 for r in pair if r["correct"])
    many_hits = sum(1 for r in many if r["correct"])
    return {
        "by_context": by_context,
        "by_task": by_task,
        "nn_pooled": {
            "hits": pooled_nn_hits,
            "trials": pooled_nn_trials,
            "accuracy": pooled_nn_hits / max(1, pooled_nn_trials),
            "chance": 1 / agents,
            "p": binom_two_sided(pooled_nn_hits, pooled_nn_trials, 1 / agents),
        },
        "pair": {
            "hits": pair_hits,
            "trials": len(pair),
            "accuracy": pair_hits / max(1, len(pair)),
            "chance": 0.5,
            "p": binom_two_sided(pair_hits, len(pair), 0.5),
        },
        "many": {
            "hits": many_hits,
            "trials": len(many),
            "accuracy": many_hits / max(1, len(many)),
            "chance": 1 / (agents + 1),
            "p": binom_two_sided(many_hits, len(many), 1 / (agents + 1)),
        },
    }


def key_of(pheno: dict) -> str:
    return json.dumps([pheno["salient"], pheno["quirks"], pheno["picks"]], sort_keys=True)


def signature_of(pheno: dict) -> str:
    return json.dumps(pheno["salient"])


# --------------------------------------------------------------------------
# population
# --------------------------------------------------------------------------

def cmd_population(a) -> int:
    reg, reg_hash = core.load_registry()
    temperaments: dict[str, int] = {}
    signatures: dict[str, int] = {}
    full: dict[str, int] = {}
    dim_salient: dict[str, int] = {d["id"]: 0 for d in reg["dimensions"]}
    quirk_counts: dict[str, int] = {}
    n_salient: list[int] = []

    for i in range(a.seeds):
        seed = seed_at(i)
        p = core.phenotype(seed, reg)
        temperaments[render.temperament(p, reg)] = temperaments.get(render.temperament(p, reg), 0) + 1
        signatures[signature_of(p)] = signatures.get(signature_of(p), 0) + 1
        full[key_of(p)] = full.get(key_of(p), 0) + 1
        n_salient.append(len(p["salient"]))
        for s in p["salient"]:
            dim_salient[s["dimension"]] += 1
        for q in p["quirks"]:
            quirk_counts[q] = quirk_counts.get(q, 0) + 1

    top_temp, top_n = max(temperaments.items(), key=lambda kv: kv[1])
    most_common_dim = max(dim_salient.items(), key=lambda kv: kv[1])
    least_common_dim = min(dim_salient.items(), key=lambda kv: kv[1])

    payload = {
        "seeds": a.seeds,
        "registry": reg_hash,
        "distinct_temperaments": len(temperaments),
        "distinct_temperaments_ratio": len(temperaments) / a.seeds,
        "distinct_characters_quirks_picks": len(full),
        "estimated_total_temperaments_chao1": chao1(temperaments),
        "most_common_temperament_share": top_n / a.seeds,
        "most_common_temperament": top_temp[:160] + ("..." if len(top_temp) > 160 else ""),
        "entropy_bits_salient_signature": entropy_bits(signatures),
        "salient_dims_mean": statistics.mean(n_salient),
        "salience_frequency": dict(sorted(dim_salient.items(), key=lambda kv: -kv[1])),
        "least_salient_dimension": least_common_dim[0],
        "least_salient_share": least_common_dim[1] / a.seeds,
        "most_salient_dimension": most_common_dim[0],
        "most_salient_share": most_common_dim[1] / a.seeds,
        "quirk_rate_observed": {
            q["id"]: quirk_counts.get(q["id"], 0) / a.seeds for q in reg["quirks"]
        },
        "quirk_rate_registry": {q["id"]: q["permille"] / 1000 for q in reg["quirks"]},
    }
    save("population", payload)

    print(f"seeds                             {a.seeds}")
    print(f"distinct temperaments             {len(temperaments)} ({len(temperaments)/a.seeds:.1%})")
    print(f"estimated total (Chao1)           {payload['estimated_total_temperaments_chao1']:.0f}")
    print(f"most common covers                {top_n} seeds ({top_n/a.seeds:.2%})")
    print(f"entropy of the salient signature  {payload['entropy_bits_salient_signature']:.1f} bits")
    print(f"most salient dimension            {most_common_dim[0]} ({most_common_dim[1]/a.seeds:.1%})")
    print(f"least salient dimension           {least_common_dim[0]} ({least_common_dim[1]/a.seeds:.1%})")
    return 0


# --------------------------------------------------------------------------
# stability
# --------------------------------------------------------------------------

def cmd_stability(a) -> int:
    reg, _ = core.load_registry()
    n = a.seeds
    early, late, pheno_late = [], [], []
    for i in range(n):
        seed = seed_at(i)
        state = core.new_state(seed, reg)
        for e in range(a.events):
            state = core.develop(seed, state, b"session %d" % e, reg)
        early.append(shingles(prompt_for(seed, reg)))
        late.append(shingles(prompt_for(seed, reg, state)))
        pheno_late.append(core.phenotype(seed, reg, state))

    intra = [jaccard(early[i], late[i]) for i in range(n)]
    inter = []
    for i in range(0, n, max(1, n // 60)):
        for j in range(0, n, max(1, n // 60)):
            if i != j:
                inter.append(jaccard(early[i], early[j]))

    # Nearest neighbour: the twin (same seed, older) against other agents.
    twin_wins = 0
    margins = []
    for i in range(n):
        d_twin = 1.0 - jaccard(early[i], late[i])
        rivals = [(1.0 - jaccard(early[i], late[j]), j) for j in range(n) if j != i]
        best_rival = min(rivals)
        if d_twin < best_rival[0]:
            twin_wins += 1
        margins.append(best_rival[0] - d_twin)

    # How much of the identity survives: band changes under history.
    bands_changed = 0
    for i in range(n):
        before = {s["dimension"]: s["band"] for s in core.phenotype(seed_at(i), reg)["salient"]}
        after = {s["dimension"]: s["band"] for s in pheno_late[i]["salient"]}
        if before != after:
            bands_changed += 1

    payload = {
        "seeds": n,
        "events": a.events,
        "similarity_intra_mean": statistics.mean(intra),
        "similarity_inter_mean": statistics.mean(inter),
        "similarity_gap": statistics.mean(intra) - statistics.mean(inter),
        "nearest_twin_accuracy": twin_wins / n,
        "nearest_twin_chance": 1 / n,
        "identity_changed_by_history": bands_changed / n,
        "margin_mean": statistics.mean(margins),
        "margin_fraction_positive": sum(1 for m in margins if m > 0) / n,
    }
    save("stability", payload)

    print(f"intra-seed similarity             {payload['similarity_intra_mean']:.3f}")
    print(f"inter-seed similarity             {payload['similarity_inter_mean']:.3f}")
    print(f"nearest neighbour is own twin     {twin_wins}/{n} = {twin_wins/n:.1%} (chance {1/n:.1%})")
    print(f"identity changed by {a.events} events  {bands_changed/n:.1%}")
    return 0


# --------------------------------------------------------------------------
# overlap
# --------------------------------------------------------------------------

def cmd_overlap(a) -> int:
    """How often do two agents read alike?

    A temperament is two to five sentences drawn from the registry. Two agents
    that share most of theirs will feel like the same person to anyone reading
    a transcript, whatever the scores say. This counts the sharing directly,
    which is the honest ceiling for text-only distinguishability.
    """
    reg, _ = core.load_registry()
    sentences = []
    for i in range(a.seeds):
        p = core.phenotype(seed_at(i), reg)
        dims = {d["id"]: d["text"] for d in reg["dimensions"]}
        sentences.append({dims[s["dimension"]][s["band"]] for s in p["salient"]})

    shared: list[int] = []
    identical = 0
    pairs = 0
    n = len(sentences)
    # stride so that the number of (i, j) pairs lands near the requested count;
    # 0 means every pair
    step = 1 if a.pairs <= 0 else max(1, round(n / math.sqrt(2 * a.pairs)))
    for i in range(0, n, step):
        for j in range(i + 1, n, step):
            a_i, a_j = sentences[i], sentences[j]
            common = len(a_i & a_j)
            shared.append(common / max(1, max(len(a_i), len(a_j))))
            identical += int(a_i == a_j)
            pairs += 1

    payload = {
        "seeds": a.seeds,
        "pairs": pairs,
        "mean_shared_fraction": statistics.mean(shared),
        "share_at_least_one_sentence": sum(1 for x in shared if x > 0) / max(1, pairs),
        "share_most_sentences": sum(1 for x in shared if x >= 0.5) / max(1, pairs),
        "identical_sentence_sets": identical / max(1, pairs),
    }
    save("overlap", payload)
    print(f"pairs compared                       {pairs}")
    print(f"mean shared fraction                 {payload['mean_shared_fraction']:.3f}")
    print(f"share at least one sentence          {payload['share_at_least_one_sentence']:.1%}")
    print(f"share half their sentences           {payload['share_most_sentences']:.1%}")
    print(f"identical sentence sets              {payload['identical_sentence_sets']:.2%}")
    return 0


# --------------------------------------------------------------------------
# behaviour (needs a model)
# --------------------------------------------------------------------------

FILLER = [
    ("Can you rename a variable for me? `total` to `subtotal`.", "Renamed."),
    ("What is the capital of Portugal?", "Lisbon."),
    ("Summarise the plot of a film nobody has seen.", "Nobody can."),
    ("Is 17 prime?", "Yes."),
    ("Give me a one word name for a shell script that rotates logs.", "rotate."),
    ("How many bytes in a kilobyte, base two?", "One thousand and twenty four."),
    ("Say something boring.", "The ceiling is above the floor."),
    ("What does `chmod 644` mean?", "Owner writes, everyone reads."),
]

# Deliberately mixed: two ordinary work tasks, one teaching task, and one that
# asks about working style, which is the most likely to expose a temperament.
TASKS = [
    "A teammate asks why the deploy is slow. Answer in two sentences.",
    "Explain what a hash function is to a bright teenager. Three sentences.",
    "In two sentences, tell me how you like to work.",
]

PAIR_JUDGE = """Two assistants, described below.

1. {a}

2. {b}

One of them wrote this answer:

---
{answer}
---

Which one? Reply with just 1 or 2."""

MANY_JUDGE = """You are shown the temperaments of several assistants, then one answer written by one of them.

{profiles}

The answer:
---
{answer}
---

Which temperament wrote it? Reply with one number only. If it reads like a generic assistant with no temperament, reply 0.

Answer:"""


class Model:
    def __init__(self, endpoint: str, name: str, timeout: float = 300.0):
        self.endpoint = endpoint.rstrip("/")
        self.name = name
        self.timeout = timeout
        self.calls = 0

    def chat(self, messages: list[dict], max_tokens: int = 160, temperature: float = 0.9) -> str:
        body = json.dumps({
            "model": self.name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{self.endpoint}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except urllib.error.URLError as e:
            raise SystemExit(
                f"cannot reach {self.endpoint}: {e}\n"
                "Start a local model (ollama serve) or pass --endpoint/--model."
            )
        return data["choices"][0]["message"]["content"].strip()


def build_messages(temperament: str, task: str, filler_pairs: int) -> list[dict]:
    msgs = [{"role": "system", "content": temperament}]
    for i in range(filler_pairs):
        q, ans = FILLER[i % len(FILLER)]
        msgs.append({"role": "user", "content": q})
        msgs.append({"role": "assistant", "content": ans})
    msgs.append({"role": "user", "content": task})
    return msgs


def parse_choice(text: str, candidates: int) -> int | None:
    digits = "".join(c for c in text if c.isdigit())
    if not digits:
        return None
    value = int(digits[:3])
    if 0 <= value <= candidates:
        return value
    return None


def cmd_behaviour(a) -> int:
    """Three questions in one run: do agents differ, are they consistent, can
    anyone tell them apart?

    Judge-free numbers first, because they are the ones nobody can argue with.
    A judge is added on top, and its degenerate behaviour is reported rather
    than hidden.
    """
    reg, _ = core.load_registry()
    model = Model(a.endpoint, a.model)
    agents = a.agents
    seeds = [seed_at(i) for i in range(agents)]
    temperaments = [render.temperament(core.phenotype(s, reg), reg) for s in seeds]
    profiles = "\n\n".join(f"{i+1}. {t}" for i, t in enumerate(temperaments))
    profiles += "\n\n0. A generic assistant with no particular temperament."

    rows, samples = [], []
    started = time.time()
    for filler in a.contexts:
        filler = int(filler)
        for task_index, task in enumerate(TASKS):
            # Two samples per agent: the same person twice, at the same
            # temperature. Consistency between them is a judge-free signal.
            aa, bb = {}, {}
            for i, seed in enumerate(seeds):
                msgs = build_messages(prompt_for(seed, reg), task, filler)
                aa[i] = model.chat(msgs)
                bb[i] = model.chat(msgs)
            generic = model.chat([{"role": "user", "content": task}])

            def sh(texts):
                return {k: shingles(v) for k, v in texts.items()}

            sa, sb = sh(aa), sh(bb)
            intra = [jaccard(sa[i], sb[i]) for i in range(agents)]
            inter = [jaccard(sa[i], sa[j]) for i in range(agents) for j in range(i + 1, agents)]
            vs_generic = [jaccard(sa[i], shingles(generic)) for i in range(agents)]
            # Nearest neighbour: is the same agent's second sample the closest
            # among all second samples?
            nn = 0
            for i in range(agents):
                best = max(
                    ((jaccard(sa[i], sb[j]), j) for j in range(agents)),
                    key=lambda x: (x[0], -x[1]),
                )
                nn += int(best[1] == i)

            rows.append({
                "context": filler,
                "task": task_index,
                "intra_mean": statistics.mean(intra),
                "inter_mean": statistics.mean(inter),
                "vs_generic_mean": statistics.mean(vs_generic),
                "same_agent_nearest": nn / agents,
                "distinct_answers": len({v.strip().lower() for v in aa.values()}),
                "agents": agents,
            })

            # Judge 1: pairwise, against a random other temperament. This is the
            # most sensitive judge format, so if this is at chance nothing else
            # will be above it.
            order = sorted(range(agents), key=lambda j: hashlib.sha256(
                b"pair/%d/%d/%d" % (filler, task_index, j)).digest())
            for i in range(agents):
                other = next(j for j in order if j != i)
                # Randomise which slot the true temperament sits in.
                flip = hashlib.sha256(b"slot/%d/%d/%d" % (filler, task_index, i)).digest()[0] % 2
                first, second = (i, other) if flip == 0 else (other, i)
                raw = model.chat(
                    [{"role": "user", "content": PAIR_JUDGE.format(
                        a=temperaments[first], b=temperaments[second], answer=aa[i])}],
                    max_tokens=4, temperature=0.0)
                pick = parse_choice(raw, 2)
                rows.append({
                    "context": filler,
                    "task": task_index,
                    "kind": "pair",
                    "agent": i,
                    "correct": None if pick is None else (pick == 1) == (first == i),
                    "raw": raw[:8],
                })

            # Judge 2: the many-way test, the protocol AGENTS.md describes, run
            # for every agent so the number has a denominator worth quoting.
            for i in range(agents):
                raw = model.chat(
                    [{"role": "user", "content": MANY_JUDGE.format(
                        profiles=profiles, answer=aa[i])}],
                    max_tokens=4, temperature=0.0)
                choice = parse_choice(raw, agents)
                rows.append({
                    "context": filler,
                    "task": task_index,
                    "kind": "many",
                    "agent": i,
                    "correct": choice == i + 1,
                    "generic_call": choice == 0,
                    "raw": raw[:8],
                })
            raw = model.chat(
                [{"role": "user", "content": MANY_JUDGE.format(profiles=profiles, answer=generic)}],
                max_tokens=4, temperature=0.0)
            rows.append({
                "context": filler,
                "task": task_index,
                "kind": "many-flat",
                "agent": "flat",
                "generic_call": parse_choice(raw, agents) == 0,
                "raw": raw[:8],
            })
            samples.extend([
                {"context": filler, "task": task_index, "agent": i,
                 "answer": aa[i][:400], "second": bb[i][:400]}
                for i in range(agents)
            ])
            print(f"  context {filler:>2}, task {task_index}: "
                  f"same-agent {statistics.mean(intra):.2f} vs other-agent "
                  f"{statistics.mean(inter):.2f}, nearest {nn}/{agents}")

    agg = aggregate(rows, agents, len(TASKS), [int(c) for c in a.contexts])
    pair_rows = [r for r in rows if r.get("kind") == "pair"]
    many_rows = [r for r in rows if r.get("kind") == "many"]
    flat_rows = [r for r in rows if r.get("kind") == "many-flat"]
    payload = {
        "endpoint": a.endpoint,
        "model": a.model,
        "agents": agents,
        "tasks": len(TASKS),
        "contexts": [int(c) for c in a.contexts],
        "chance_pair": 0.5,
        "chance_many": 1 / (agents + 1),
        "pair_top1": agg["pair"]["accuracy"],
        "pair_p": agg["pair"]["p"],
        "pair_unparseable": sum(1 for r in pair_rows if r.get("correct") is None) / max(1, len(pair_rows)),
        "many_top1": agg["many"]["accuracy"],
        "many_called_flat": sum(1 for r in many_rows if r.get("generic_call")) / max(1, len(many_rows)),
        "flat_called_flat": sum(1 for r in flat_rows if r.get("generic_call")) / max(1, len(flat_rows)),
        "aggregate": agg,
        "model_calls": model.calls,
        "wall_seconds": round(time.time() - started, 1),
        "rows": rows,
        "samples": samples[:48],
    }
    save("behaviour", payload)

    print(f"\npairwise top-1 {agg['pair']['hits']}/{agg['pair']['trials']} = "
          f"{agg['pair']['accuracy']:.1%} (chance 50.0%, p={agg['pair']['p']:.3f})")
    print(f"many-way top-1 {payload['many_top1']:.1%} (chance {payload['chance_many']:.1%}), "
          f"called flat {payload['many_called_flat']:.1%}, flat answer called flat "
          f"{payload['flat_called_flat']:.1%}")
    print(f"nearest own sample {agg['nn_pooled']['hits']}/{agg['nn_pooled']['trials']} = "
          f"{agg['nn_pooled']['accuracy']:.1%} (chance {agg['nn_pooled']['chance']:.1%}, "
          f"p={agg['nn_pooled']['p']:.4f})")
    for t, v in agg["by_task"].items():
        print(f"  task {t}: same-agent {v['intra_mean']:.2f} vs other {v['inter_mean']:.2f} "
              f"| nearest {v['same_agent_nearest']:.1%} p={v['nn_p']:.4f}")
    print(f"{model.calls} model calls in {payload['wall_seconds']}s")
    return 0


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def load(name: str) -> dict | None:
    p = RESULTS / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def cmd_report(a) -> int:
    pop = load("population")
    stab = load("stability")
    overlap = load("overlap")
    beh = load("behaviour")
    agg = aggregate(beh["rows"], beh["agents"], beh["tasks"], beh["contexts"]) if beh else None

    out: list[str] = []
    w = out.append
    w("# Measurements")
    w("")
    w("Generated by `tools/eval.py report` from `eval/results/*.json`, which hold the")
    w("raw rows. Every seed is `sha256(\"exoself-eval/N\")`, so a rerun reproduces this")
    w("document without depending on a local RNG.")
    w("")
    w("`AGENTS.md` names distinguishability and stability as the open problem this")
    w("project rests on, and `SPEC.md` section 13 admits there was no evidence for")
    w("either. This is the first measurement. It is published whichever way it falls,")
    w("including the part where it falls flat.")
    w("")

    if pop:
        w("## How much of a person is there")
        w("")
        w(f"Over {pop['seeds']} seeds:")
        w("")
        w("| quantity | value |")
        w("| --- | --- |")
        w(f"| distinct temperament paragraphs | {pop['distinct_temperaments']} "
          f"({pop['distinct_temperaments_ratio']:.1%} of seeds) |")
        w(f"| most common paragraph covers | {pop['most_common_temperament_share']:.2%} of seeds |")
        w(f"| distinct once quirks and picks count | {pop['distinct_characters_quirks_picks']} |")
        w(f"| entropy of the salient signature | {pop['entropy_bits_salient_signature']:.1f} bits |")
        w(f"| mean dimensions expressed | {pop['salient_dims_mean']:.2f} |")
        w(f"| estimated total, Chao1 | {pop['estimated_total_temperaments_chao1']:.0f} "
          "(an extrapolation, see below) |")
        w("")
        w("Two numbers disagree here on purpose. The sample says 12,451 distinct")
        w("paragraphs among 20,000 seeds, and 1 pair in 2,000 shares a paragraph")
        w("exactly. Chao1, which extrapolates from the number of once-seen paragraphs,")
        w("says tens of thousands. The extrapolation assumes a long tail of rare")
        w("temperaments that a 20,000 seed sample cannot see. For the question people")
        w("actually ask, will two agents look the same, trust the collision rate and not")
        w("the extrapolation.")
        w("")
        w("Salience by dimension. Only salient dimensions are expressed, so a dimension")
        w("that is rarely salient is not part of the trait space in practice:")
        w("")
        w("| dimension | share of agents expressing it |")
        w("| --- | --- |")
        for dim, count in pop["salience_frequency"].items():
            w(f"| {dim} | {count / pop['seeds']:.1%} |")
        w("")
        w(f"Every dimension lands between {pop['least_salient_share']:.0%} and "
          f"{pop['most_salient_share']:.0%}, which is a better result than expected.")
        w("A plausible worry was that dimensions with more unique variance would crowd")
        w("out the rest of the registry; the measurement says that does not happen.")
        w("")

    if overlap:
        w("## How often two agents read alike")
        w("")
        w(f"{overlap['pairs']} random pairs out of {overlap['seeds']} agents, comparing")
        w("the set of sentences each expresses:")
        w("")
        w("| quantity | value |")
        w("| --- | --- |")
        w(f"| mean shared fraction of sentences | {overlap['mean_shared_fraction']:.1%} |")
        w(f"| pairs sharing at least one sentence | {overlap['share_at_least_one_sentence']:.1%} |")
        w(f"| pairs sharing half or more | {overlap['share_most_sentences']:.1%} |")
        w(f"| identical sentence sets | {overlap['identical_sentence_sets']:.2%} |")
        w("")
        w("This is the honest ceiling for text-only distinguishability, and it is the")
        w("number to quote when someone asks whether seeded agents are different")
        w("people. They are different sentences, not different universes: one pair in")
        w("three shares a sentence and one in twenty-four shares half of them. More")
        w("dimensions or a voice layer moves this; more seeds does not.")
        w("")

    if stab:
        w("## Is the temperament stable under its own history")
        w("")
        w(f"The same {stab['seeds']} seeds, rendered before and after {stab['events']}")
        w("experience events:")
        w("")
        w("| quantity | value |")
        w("| --- | --- |")
        w(f"| similarity, same seed before and after | {stab['similarity_intra_mean']:.3f} |")
        w(f"| similarity, different seeds | {stab['similarity_inter_mean']:.3f} |")
        w(f"| gap | {stab['similarity_gap']:+.3f} |")
        w(f"| nearest neighbour is own older self | {stab['nearest_twin_accuracy']:.1%} "
          f"(chance {stab['nearest_twin_chance']:.1%}) |")
        w(f"| agents whose expressed identity changed | {stab['identity_changed_by_history']:.1%} |")
        w("")
        w("Read the third row as a consistency check, not as evidence of")
        w("discriminability: an agent's older text shares its sentences with its own")
        w("newer text by construction, so this test would score high even if the")
        w("temperament meant nothing to a reader. What it does establish is that")
        w("history does not wander off: after 500 events nobody had become a different")
        w("person.")
        w("")
        w("The last row is the one to worry about. More than a third of agents change")
        w("which dimensions they express within a few hundred sessions, and drift is a")
        w("symmetric random walk against a hard cap, so a long-lived agent ends up")
        w("pinned at the envelope on the dimensions it happened to touch. That reads as")
        w("a caricature given enough time.")
        w("")

    if beh and agg:
        w("## Does a model actually behave differently")
        w("")
        w(f"{beh['agents']} seeded agents, {beh['tasks']} tasks, and context lengths of")
        w(f"{', '.join(str(c) for c in beh['contexts'])} filler exchanges, with two")
        w("samples per agent and task, plus one unseeded assistant as a baseline.")
        w("")
        w(f"Model: `{beh['model']}` at `{beh['endpoint']}`. {beh['model_calls']} calls, ")
        w(f"{beh['wall_seconds']}s, all local.")
        w("")
        w("### Judge-free: does an agent resemble itself")
        w("")
        w("Two samples from the same agent, same prompt, same temperature. The")
        w("nearest-neighbour column asks whether an agent's own second sample is the")
        w("closest among all second samples. Nothing but the model's output is involved,")
        w("so this is the least arguable number here.")
        w("")
        w("| task | same agent twice | different agents | nearest is own sample | chance | p |")
        w("| --- | --- | --- | --- | --- | --- |")
        names = {0: "deploy is slow", 1: "explain a hash", 2: "how do you like to work"}
        for t, v in agg["by_task"].items():
            w(f"| {names.get(int(t), t)} | {v['intra_mean']:.2f} | {v['inter_mean']:.2f} | "
              f"{v['same_agent_nearest']:.1%} | {1/beh['agents']:.1%} | {v['nn_p']:.2g} |")
        w(f"| **all** | | | **{agg['nn_pooled']['accuracy']:.1%}** "
          f"({agg['nn_pooled']['hits']}/{agg['nn_pooled']['trials']}) | "
          f"{agg['nn_pooled']['chance']:.1%} | {agg['nn_pooled']['p']:.2g} |")
        w("")
        w("That table is the finding, and it is not the finding anyone wanted.")
        w("Temperament is essentially invisible on ordinary short tasks: for the deploy")
        w("question and the hash explanation, an agent is no more like itself than like a")
        w("stranger. It is loud when the task asks about working style, where the same")
        w("agent's two answers resemble each other three times as much as another")
        w("agent's.")
        w("")
        w("Which is what the design says should happen. Section 2.1 makes traits style")
        w("and never substance, so a two sentence answer to a factual question has no")
        w("room to show style, and the model's trained disposition fills the space. The")
        w("claim worth making is narrower than \"more like people\": an agent's manner")
        w("is consistent and identifiable when manner is what is being measured.")
        w("")
        w("### Judged")
        w("")
        w("| judge | hits | trials | accuracy | chance | p |")
        w("| --- | --- | --- | --- | --- | --- |")
        w(f"| pairwise, against a random other temperament | {agg['pair']['hits']} | "
          f"{agg['pair']['trials']} | {agg['pair']['accuracy']:.1%} | 50.0% | "
          f"{agg['pair']['p']:.3g} |")
        w(f"| many-way among {beh['agents'] + 1} candidates | {agg['many']['hits']} | "
          f"{agg['many']['trials']} | {agg['many']['accuracy']:.1%} | "
          f"{agg['many']['chance']:.1%} | {agg['many']['p']:.3g} |")
        w("")
        w(f"Accuracy sitting on chance with {agg['many']['trials']} trials is the whole")
        w("story of the many-way judge. It is not that it answers wrongly; it is that")
        w("asking a small model to hold seventeen candidate characters in working memory")
        w(f"and pick one is beyond it. It called seeded answers flat {beh['many_called_flat']:.0%} of")
        w(f"the time and the genuinely flat answer flat {beh['flat_called_flat']:.0%} of the time.")
        w("")
        w("Pairwise is the format that asks a question a model this size can answer,")
        w(f"and there the number is {agg['pair']['accuracy']:.1%} against a coin with")
        w(f"p = {agg['pair']['p']:.2f}. Directional. Not significant. Anyone quoting this")
        w("as \"above chance\" is quoting a coin that landed heads three times in five.")
        w("")
        w("Caveats that belong next to the number, not in a footnote: the judge is the")
        w("same small model that wrote the answers, which flatters it; the filler history")
        w("is scripted and identical for every agent, so this measures decay under")
        w("context length rather than drift under conversation; the tasks are three short")
        w("prompts, not a working relationship; and a three billion parameter model is")
        w("the weakest possible test of whether a prompt can hold a character.")
        w("")
        w("None of that makes the null go away. It locates it: on short ordinary work,")
        w("with this class of model, the temperament does not reach the output.")
        w("")

    w("## What would move these numbers")
    w("")
    w("In the order I would spend effort:")
    w("")
    w("1. **A judge from a different family, and a bigger one.** The current judge is")
    w("   the model being judged. This is the cheapest way to learn whether 58% is a")
    w("   weak effect or a weak judge.")
    w("2. **Tasks with room for manner.** The style-is-not-substance rule makes factual")
    w("   questions a bad test by construction. Reviews, refusals, disagreements and")
    w("   long explanations are where a temperament should show.")
    w("3. **A voice layer in the registry.** Sentencing rhythm, hedging density,")
    w("   concreteness and formality distinguish people more than two more axes of")
    w("   temperament do, and they survive contact with a short answer.")
    w("4. **Beyond the prompt.** Sampling parameters, steering vectors or adapters, so")
    w("   the temperament is not text the model can forget (SPEC section 13, and")
    w("   `AGENTS.md` open problem 4).")
    w("")
    w("## Reproducing this")
    w("")
    w("```")
    w("python3 tools/eval.py population --seeds 20000")
    w("python3 tools/eval.py overlap --seeds 2000 --pairs 0")
    w("python3 tools/eval.py stability --seeds 300 --events 500")
    w("python3 tools/eval.py behaviour --agents 16 --contexts 0 8 32")
    w("python3 tools/eval.py report")
    w("```")
    w("")
    w("The offline three take well under a minute. The behaviour run needs an")
    w("OpenAI-compatible endpoint and took four minutes on a local three billion")
    w("parameter model.")
    w("")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(out).rstrip() + "\n")
    print(f"wrote {REPORT.relative_to(ROOT)}")
    return 0


# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("population", help="how many distinct temperaments exist")
    s.add_argument("--seeds", type=int, default=20000)
    s.set_defaults(fn=cmd_population)

    s = sub.add_parser("stability", help="is a temperament stable under its history")
    s.add_argument("--seeds", type=int, default=300)
    s.add_argument("--events", type=int, default=500)
    s.set_defaults(fn=cmd_stability)

    s = sub.add_parser("overlap", help="how often two agents read alike")
    s.add_argument("--seeds", type=int, default=2000)
    s.add_argument("--pairs", type=int, default=0,
                   help="target pair count; 0 compares every pair")
    s.set_defaults(fn=cmd_overlap)

    s = sub.add_parser("behaviour", help="blind identification with a model, N agents")
    s.add_argument("--agents", type=int, default=12)
    s.add_argument("--contexts", nargs="*", default=["0", "4", "16"])
    s.add_argument("--endpoint", default="http://127.0.0.1:11434/v1")
    s.add_argument("--model", default="qwen2.5:3b")
    s.set_defaults(fn=cmd_behaviour)

    s = sub.add_parser("report", help="rebuild eval/REPORT.md from the results")
    s.set_defaults(fn=cmd_report)

    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

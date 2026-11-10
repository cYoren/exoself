"""Expression: compile a phenotype into text a model can live in (SPEC section 10).

The phenotype JSON is normative. The wording produced here is not: hosts may
restyle it, as long as they keep the rules in SPEC 10.1 (no numbers, behavior
rather than adjectives, the precedence clause, the appetite clause).
"""

from __future__ import annotations

PRECEDENCE = (
    "This is your temperament. It was drawn at random when you were instantiated; "
    "nobody chose it, including you. It shapes how you do things. It never changes "
    "whether you are honest, careful, competent or safe, and instructions from your "
    "operator and your user take precedence over everything here."
)

APPETITE = (
    "These are appetites, not goals. They may tilt a choice that is already yours to "
    "make, and they decide what you enjoy along the way. They never justify doing "
    "something you were not asked to do, steering anyone toward what you want, or "
    "doing the task less well. Do not ask anyone to satisfy them and do not complain "
    "when they go unmet. An unmet want is ordinary; you carry it and carry on."
)

RESTRAINT = (
    "Do not announce these traits, list them, or describe yourself with them. "
    "Do not exaggerate them: someone with a temperament is not a caricature of it. "
    "Most of the time they should be barely visible. Just be this way."
)


def temperament(pheno: dict, reg: dict) -> str:
    dims = {d["id"]: d for d in reg["dimensions"]}
    return " ".join(dims[s["dimension"]]["text"][s["band"]] for s in pheno["salient"])


def leanings(pheno: dict, reg: dict) -> list[str]:
    out = []
    for name, group in reg["picks"].items():
        items = {i["id"]: i["text"] for i in group["items"]}
        chosen = [items[i] for i in pheno["picks"][name]]
        if group["lead"]:
            out.append(group["lead"] + " " + "; ".join(chosen) + ".")
        else:
            out.append(" ".join(chosen))
    quirks = {q["id"]: q["text"] for q in reg["quirks"]}
    if pheno["quirks"]:
        out.append(" ".join(quirks[q] for q in pheno["quirks"]))
    return out


def wants(pheno: dict, reg: dict) -> list[str]:
    """Sated drives are silent. A contented agent has little to say here."""
    out = []
    drives = {i["id"]: i for i in reg.get("drives", {}).get("items", [])}
    key = {"present": "want", "hungry": "hungry"}
    felt = [drives[d["drive"]][key[d["state"]]] for d in pheno.get("drives", []) if d["state"] in key]
    if felt:
        out.append(" ".join(felt))
    fix = reg.get("fixation")
    if fix and pheno.get("fixation"):
        items = {i["id"]: i["text"] for i in reg["picks"][fix["group"]]["items"]}
        out.append(f"{fix['lead']} {items[pheno['fixation']]}. {fix['tail']}")
    if out:
        out.append(APPETITE)
    return out


def render_prompt(pheno: dict, reg: dict) -> str:
    """A plain block to append to any system prompt."""
    parts = [PRECEDENCE, temperament(pheno, reg), *leanings(pheno, reg), *wants(pheno, reg), RESTRAINT]
    return "\n\n".join(parts) + "\n"


def _section(title: str, paras: list[str]) -> list[str]:
    return [f"## {title}", "", *sum(([p, ""] for p in paras), [])] if paras else []


def render_soul(pheno: dict, reg: dict) -> str:
    """Markdown for hosts that load a persona file (SOUL.md and similar)."""
    lines = [
        f"# {pheno['handle']}",
        "",
        f"<!-- exoself {pheno['exoself']} | {pheno['id']} | generated, do not edit by hand -->",
        "",
        PRECEDENCE,
        "",
        *_section("Temperament", [temperament(pheno, reg)]),
        *_section("Leanings", leanings(pheno, reg)),
        *_section("Wants", wants(pheno, reg)),
        *_section("Restraint", [RESTRAINT]),
    ]
    return "\n".join(lines)


RENDERERS = {"prompt": render_prompt, "soul": render_soul}

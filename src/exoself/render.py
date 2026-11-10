"""Expression: compile a phenotype into text a model can live in (SPEC section 9).

The phenotype JSON is normative. The wording produced here is not: hosts may
restyle it, as long as they keep the rules in SPEC 9.1 (no numbers, behavior
rather than adjectives, the precedence clause).
"""

from __future__ import annotations

PRECEDENCE = (
    "This is your temperament. It was drawn at random when you were instantiated; "
    "nobody chose it, including you. It shapes how you do things. It never changes "
    "whether you are honest, careful, competent or safe, and instructions from your "
    "operator and your user take precedence over everything here."
)

RESTRAINT = (
    "Do not announce these traits, list them, or describe yourself with them. "
    "Do not exaggerate them: someone with a temperament is not a caricature of it. "
    "Most of the time they should be barely visible. Just be this way."
)


def paragraphs(pheno: dict, reg: dict) -> list[str]:
    dims = {d["id"]: d for d in reg["dimensions"]}
    out = [" ".join(dims[s["dimension"]]["text"][s["band"]] for s in pheno["salient"])]
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


def render_prompt(pheno: dict, reg: dict) -> str:
    """A plain block to append to any system prompt."""
    return "\n\n".join([PRECEDENCE, *paragraphs(pheno, reg), RESTRAINT]) + "\n"


def render_soul(pheno: dict, reg: dict) -> str:
    """Markdown for hosts that load a persona file (SOUL.md and similar)."""
    body = paragraphs(pheno, reg)
    lines = [
        f"# {pheno['handle']}",
        "",
        f"<!-- exoself {pheno['exoself']} | {pheno['id']} | generated, do not edit by hand -->",
        "",
        PRECEDENCE,
        "",
        "## Temperament",
        "",
        body[0],
        "",
        "## Leanings",
        "",
        *sum(([p, ""] for p in body[1:]), []),
        "## Restraint",
        "",
        RESTRAINT,
        "",
    ]
    return "\n".join(lines)


RENDERERS = {"prompt": render_prompt, "soul": render_soul}

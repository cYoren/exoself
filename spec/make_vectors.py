"""Regenerate spec/vectors.json from the reference implementation.

Only run this when the spec or the core registry changes on purpose. A change
in this file's output is a breaking change and needs a version bump.
"""

import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from exoself import core  # noqa: E402

reg, reg_hash = core.load_registry()
seeds = [bytes(32), b"\xff" * 32, bytes(range(32))]
seeds += [hashlib.sha256(b"exoself vector %d" % i).digest() for i in range(9)]

vectors = [{"seed": s.hex(), "phenotype": core.phenotype(s, reg)} for s in seeds]

def developed(seed: bytes, mode: str, events: int) -> dict:
    """Replay a fixed event log under one development mode (SPEC 8.3)."""
    state = core.new_state(seed, reg)
    state["develop"] = mode
    held = core.derive_drives(seed, reg)
    log = []
    for i in range(events):
        # Every seventh event satisfies one held drive, in rotation.
        met = [held[(i // 7) % len(held)]] if i % 7 == 6 else []
        log.append({"event": "event %d" % i, "satisfied": met})
        state = core.develop(seed, state, b"event %d" % i, reg, met)
    return {"seed": seed.hex(), "log": log, "state": state,
            "phenotype": core.phenotype(seed, reg, state)}


# The 0.2 walk, pinned so a state file written before 0.3 still replays.
vectors.append(developed(seeds[3], core.DEVELOP_WALK, 200))
# The mean-reverting step, long enough for the pull to matter.
vectors.append(developed(seeds[4], core.DEVELOP_MEAN_REVERTING, 400))

# Primitive checkpoints, so a port can be debugged from the bottom up.
z = bytes(32)
primitives = {
    "seed": z.hex(),
    "stream('id', 40)": core.stream(z, "id", 40).hex(),
    "u32('quirk/units')": core.u32(z, "quirk/units"),
    "gauss('factor/rigor')": core.gauss(z, "factor/rigor"),
    "gauss('unique/warmth')": core.gauss(z, "unique/warmth"),
    "drive_rate('teach')": core.drive_rate(z, "teach"),
    "drive_initial('teach')": core.drive_initial(z, "teach"),
    "fixation at events 0, 100, 1000": [core.fixation(z, reg, n) for n in (0, 100, 1000)],
    "grievance('')": core.grievance_words(""),
    "grievance('it has been a while')": core.grievance_words("it has been a while"),
}

out = pathlib.Path(__file__).with_name("vectors.json")
out.write_text(json.dumps({
    "exoself": core.SPEC_VERSION,
    "registry": core.DEFAULT_REGISTRY,
    "registry_sha256": reg_hash,
    "primitives": primitives,
    "vectors": vectors,
}, indent=2) + "\n")
print(f"wrote {len(vectors)} vectors to {out}")

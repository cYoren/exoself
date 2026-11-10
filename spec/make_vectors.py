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

# One vector with development applied, to pin the drift rules.
s = seeds[3]
state = core.new_state(s)
for i in range(200):
    state = core.develop(state, b"event %d" % i, reg)
vectors.append({
    "seed": s.hex(),
    "events": ["event %d" % i for i in range(200)],
    "chain": state["chain"],
    "drift": state["drift"],
    "phenotype": core.phenotype(s, reg, state["drift"]),
})

# Primitive checkpoints, so a port can be debugged from the bottom up.
z = bytes(32)
primitives = {
    "seed": z.hex(),
    "stream('id', 40)": core.stream(z, "id", 40).hex(),
    "u32('quirk/units')": core.u32(z, "quirk/units"),
    "gauss('factor/rigor')": core.gauss(z, "factor/rigor"),
    "gauss('unique/warmth')": core.gauss(z, "unique/warmth"),
}

out = pathlib.Path(__file__).with_name("vectors.json")
out.write_text(json.dumps({
    "exoself": core.SPEC_VERSION,
    "registry": "core@0.1",
    "registry_sha256": reg_hash,
    "primitives": primitives,
    "vectors": vectors,
}, indent=2) + "\n")
print(f"wrote {len(vectors)} vectors to {out}")

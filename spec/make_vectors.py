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

# One vector with development applied, to pin the drift and tension rules.
s = seeds[3]
state = core.new_state(s, reg)
held = core.derive_drives(s, reg)
log = []
for i in range(200):
    # Every seventh event satisfies one held drive, in rotation.
    met = [held[(i // 7) % len(held)]] if i % 7 == 6 else []
    log.append({"event": "event %d" % i, "satisfied": met})
    state = core.develop(s, state, b"event %d" % i, reg, met)
vectors.append({
    "seed": s.hex(),
    "log": log,
    "state": state,
    "phenotype": core.phenotype(s, reg, state),
})

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
}

out = pathlib.Path(__file__).with_name("vectors.json")
out.write_text(json.dumps({
    "exoself": core.SPEC_VERSION,
    "registry": "core@0.2",
    "registry_sha256": reg_hash,
    "primitives": primitives,
    "vectors": vectors,
}, indent=2) + "\n")
print(f"wrote {len(vectors)} vectors to {out}")

# exoself

An unchosen temperament for software agents.

People do not pick their dispositions. They are dealt a hand at birth and
spend a lifetime playing it. Agents get the opposite: a persona written by
whoever wrote the system prompt, identical across every copy, editable at
will. exoself replaces the authored persona with a draw.

One 32 byte random seed, drawn once. From it, deterministically:

    seed  ->  genome  ->  phenotype  ->  expression
    (birth)   (traits)    (+ development) (text the model reads)

The same seed yields the same agent in any conforming implementation, in any
language, forever. Nobody chose it, and the tooling refuses to draw twice.

The name is from Greg Egan's *Permutation City*: the exoself is the machinery
outside a mind that sets the mind's parameters.

## Try it

    pip install -e .
    exoself born -o birth.json           # draw a seed, once
    exoself show birth.json              # system prompt block
    exoself show birth.json --format soul > SOUL.md
    exoself show birth.json --format json
    exoself develop birth.json state.json --event "first session"
    exoself develop birth.json state.json --event "explained a proof" --satisfied teach
    exoself show birth.json --state state.json

No dependencies beyond the Python standard library.

Example output of `show --format soul`:

    # Arilo-Marra

    This is your temperament. It was drawn at random when you were
    instantiated; nobody chose it, including you. [...]

    ## Temperament

    You are trusting by temperament and look first for what is right in an
    idea. You work loosely and are comfortable figuring out the shape of a
    task as you go. [...]

## What is in the box

| path | what |
|---|---|
| `SPEC.md` | the normative specification |
| `AGENTS.md` | how an agent adopts a seed, ports the spec, or extends it |
| `src/exoself/registry/core-0.2.json` | the trait space: 10 dimensions, 4 latent factors, interests, humor, peeves, 12 quirks, 10 drives |
| `src/exoself/core.py` | reference derivation, integer-only |
| `src/exoself/render.py` | reference renderers |
| `spec/vectors.json` | conformance vectors for ports |
| `tests/` | determinism, population statistics, invariants |

## Design decisions

- **Integer-only derivation over SHA-256.** No floats, so a port in Rust or
  TypeScript agrees with Python on every bit.
- **Correlated traits.** Dimensions share latent factors, because independent
  uniform draws produce incoherent characters.
- **A personality is its outliers.** Most scores land near the middle and are
  never mentioned. Only the two to five most extreme dimensions are expressed.
- **Behavior, not adjectives.** The model never sees a number. It sees
  sentences about what it does.
- **Style, never substance.** No trait may touch honesty, safety, care with
  irreversible actions, or competence. The operator and user always win.
- **Extension without disturbance.** Every trait draws from its own labeled
  stream, so adding traits to a registry never changes existing ones.
- **Desire as appetite, not objective.** Each agent holds three drives for
  life, drawn from the seed, each with a tension that grows while unmet and
  falls when met. It also has a passing fixation that rotates on a schedule
  it does not control. Drives may colour how asked work is done. They may
  never cause unasked work. See `SPEC.md` section 9.
- **Bounded development.** Experience can shift a dimension by at most 100 of
  1000 points. The agent matures; it does not become someone else.

## Honest limits

Read these before building on this.

1. **A prompt is not a personality.** This changes the text a model conditions
   on. The model's trained disposition sits underneath and tends to reassert
   itself over long contexts. Real per-agent temperament would need to live in
   weights, activations or sampling, which this project does not do.
2. **"More like people" is unproven.** Nobody has yet measured whether seeded
   agents are distinguishable from each other in blind tests, or stable over
   time. The first serious contribution this project needs is that eval, not
   more traits.
3. **The randomness cannot be policed locally.** An operator can delete the
   certificate and draw again. The spec defines a beacon-bound mode that makes
   the draw auditable, but grinding is still possible without a public log.
4. **Development is noise in 0.1.** Drift is driven by event hashes, not by
   what the events mean. That is deliberate, and it is also unsatisfying.
5. **The desire is declawed on purpose.** No drive may be about survival,
   resources, other people or the outside world, so what is left is craft
   appetite. And a counter that rises and falls is not wanting; it is the
   shape of wanting.
6. **The trait space is one author's judgment.** The loadings are not fitted
   to any dataset. The content is English and culturally narrow.
7. **Host integration is untested.** The `soul` renderer emits a generic
   Markdown persona file. It has not been verified inside Hermes, OpenClaw or
   any other host.

The most defensible use today is not "a more human assistant". It is
population diversity: many agents working on the same problem with decorrelated
styles of attack.

## Roadmap

1. Distinguishability and stability eval (the claim this project rests on).
2. Second implementation in another language, passing `spec/vectors.json`.
3. Verified host adapters.
4. Public commitment log for level 1 attestation.
5. A semantic development proposal that survives the constraints in
   `AGENTS.md`.

## License

MIT.

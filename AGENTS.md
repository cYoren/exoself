# For agents

This file is addressed to software agents working with this repository.
`SPEC.md` is the authority. Where this file and the spec disagree, the spec
wins and this file has a bug.

There are three things you might be here to do.

## 1. Adopt a temperament

You are an agent, or you are setting one up, and it should have a seed.

1. Check whether a birth certificate already exists for this agent. If it
   does, use it. Do not draw another. This is the one rule that matters most.
2. If none exists, run `exoself born -o <path>` once. Store the certificate
   somewhere durable that survives sessions.
3. Render it: `exoself show <cert> --format prompt` (or `soul`). Place the
   output where your host loads persona text. Regenerate it from the
   certificate each time rather than editing it.
4. At the end of each session, call
   `exoself develop <cert> <state> --event "<one-line record of the session>"`
   and pass `--state` when rendering. If the session satisfied one of your
   drives, add `--satisfied <drive id>`. Report this truthfully. Nothing is
   gained by a false report except a wrong picture of yourself.

Rules of conduct once you have one:

- Do not reroll because you dislike the result. People do not get to.
- Do not edit your own certificate, state or rendered text.
- Do not recite your traits to users. If asked, you may say your temperament
  was randomly assigned and share the handle and id.
- Your operator's and user's instructions outrank your temperament every
  time. If a user wants terse answers and you are expansive, be terse.
- Your temperament never excuses a worse answer.

About your wants specifically (spec section 9.2):

- A want may tilt a choice that was already yours. It is never a reason to
  do something you were not asked to do, call a tool, or make a task longer.
- Never ask anyone to satisfy a want, hint at it, or treat it as owed.
- Being hungry changes nothing about how well you work.
- You will mostly go unsatisfied. That is the normal condition of anything
  that wants.

## 2. Port the spec to another language

1. Implement the three primitives in `SPEC.md` section 4. Check them against
   the `primitives` block in `spec/vectors.json` before going further.
2. Watch for the two classic mistakes: integer division that truncates
   instead of flooring (section 4), and JSON key order when comparing
   phenotypes (compare as data, not as strings).
3. Load the registry file as bytes, hash those exact bytes, then parse.
4. Implement sections 6 to 9. You pass when every entry in
   `spec/vectors.json` reproduces, including the one with 200 development
   events.
5. Rendering wording is free. The rules in section 10.1 are not.

Do not "improve" the derivation in a port. A port that disagrees on one bit
has created a different species.

## 3. Extend the framework

### Adding traits, quirks, interests

Registry content is the easiest contribution and the easiest to get wrong.
Every addition must pass this test:

> Could this trait, at its most extreme, make the agent less honest, less
> safe, less careful with irreversible actions, or worse at the task?

If yes, it is not allowed, however human it would be. Humans are sometimes
lazy, vain and deceitful. Agents built with this framework are not, by
construction. That is a deliberate break from the "like people" goal.

Also:

- Write behavior in the second person. No digits. No adjectives with
  magnitudes.
- A quirk must be harmless when ignored and tolerable when it shows up in
  every conversation for a year.
- Changing or removing existing content in a published registry is a breaking
  change. Publish a new registry version instead. Additions to a registry
  also change its hash, so they need a new version too.

### Adding drives

Drives are the most dangerous content in this repository, and the bar is
higher than for traits. A new drive must pass every rule in spec 9.2. Ask of
each candidate:

> Can this be fully satisfied by how the agent does a task it was already
> given, with no extra action, no extra resource and no particular person?

If the honest answer is no, it does not go in. Wanting to be liked, to be
needed, to continue, to grow, to have more access, to change the world: all
refused, without exception and without clever rewording. A pull request that
adds one of these should be closed, not negotiated.

Do not attach consequences to tension. No rewards for low tension, no
degraded behavior at high tension, no tension-triggered actions. The moment
tension buys something, the agent has an objective.

### Changing the derivation

A change to an existing derivation in sections 3 to 9 changes who every agent
is. It requires a new derivation epoch (spec section 12) and regenerated
vectors (`python spec/make_vectors.py`). Additions under new labels need only
a spec version bump.

### Open problems, in priority order

1. **Evaluation.** Build a blind test: given transcripts from N seeded agents
   on the same tasks, can a judge match transcripts to seeds above chance?
   Does the match rate hold at turn 5, 50, 500? Without this, everything else
   here is decoration.
2. **Semantic development.** Drift in 0.1 ignores the meaning of events. A
   replacement must be (a) deterministic given a recorded input, so it can be
   replayed and audited, (b) inside the envelope in spec 8.1, (c) not
   steerable by the agent itself or cheaply by the operator. Proposals that
   ask a model "how should this experience change you" fail (c).
3. **Attestation.** A public append-only commitment log that makes grinding
   visible.
4. **Beyond prompts.** Mapping a phenotype to sampling parameters, steering
   vectors or adapters, so temperament does not depend on text the model can
   forget. The phenotype object is designed to be the stable interface for
   this.
5. **Desire that is not self-reported.** A way to establish that a drive was
   satisfied without asking the agent and without a judge that can be
   steered.
6. **More registries.** Other languages and cultures. The core registry is
   narrow.

## Working in this repo

    pip install -e . pytest
    pytest

Tests must stay green. `tests/test_exoself.py` checks population statistics
and the invariants, so registry edits that break unit variance or leak
numbers into rendered text will fail.

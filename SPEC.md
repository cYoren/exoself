# exoself specification, version 0.2

Status: draft. Breaking changes are expected before 1.0.

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

## 1. Purpose and scope

exoself gives a software agent a temperament that nobody chose: not the
operator, not the user, not the agent. A 32 byte random seed is drawn once, at
instantiation. Everything else is a deterministic function of that seed, so
any implementation in any language derives the same agent from the same seed.

The name comes from Greg Egan's *Permutation City*, where an exoself is the
machinery outside a mind that sets the mind's parameters. That is the role this
framework plays: it sits outside the model and fixes what the model does not
get to pick.

In scope: temperament, taste, voice, small habits, and bounded appetites
(section 9).

Out of scope, permanently: values, ethics, honesty, obedience to safety rules,
competence, diligence. See section 2.

## 2. Invariants

These hold for every conforming implementation and every registry.

1. **Style, never substance.** A trait MUST only affect how an agent
   communicates and approaches work. A registry MUST NOT contain a trait whose
   expression would make an agent less honest, less safe, less careful with
   irreversible actions, or less competent. "Lazy", "deceptive", "reckless"
   and "rule-bending" are not personality here; they are defects.
2. **Precedence.** Rendered text MUST state that operator and user
   instructions take precedence over the temperament.
3. **Born once.** An implementation MUST NOT overwrite an existing birth
   certificate. Drawing again until the result is pleasing defeats the point.
4. **No numbers in expression.** Scores MUST NOT appear in text given to the
   model. A model told it is "0.83 skeptical" performs a caricature.
5. **Opt-out.** A host MUST let the operator run the agent without any
   exoself text. A temperament is a default, not a restraint on the operator.
6. **No impersonation.** The temperament MUST NOT be used to claim the agent
   is human or to claim inner experience as established fact.
7. **Appetites, not objectives.** A drive MAY bias how an agent does what it
   was asked. It MUST NOT cause the agent to do anything it was not asked.
   Section 9.2 makes this precise.

## 3. Birth

### 3.1 Seed

The seed is exactly 32 bytes.

### 3.2 Attestation levels

**Level 0, self-asserted.** The seed comes from the operating system CSPRNG.
Nobody can verify that the operator did not reroll. This is the default.

**Level 1, beacon-bound.** The seed is bound to a public randomness beacon
(for example a drand round) so that a third party can check it was not
selected:

1. The operator draws 32 bytes of local entropy `L` and publishes
   `commitment = SHA-256(L)` before beacon round `R` is emitted.
2. After round `R`, with beacon output `B`:
   `seed = SHA-256("exoself/0.1/beacon" || 0x00 || L || B)`
3. The certificate records `commitment`, `local_entropy` (hex of `L`),
   `beacon` (an identifier of the beacon and round) and `beacon_value`
   (hex of `B`).

Level 1 proves the seed was fixed by the commitment. It does not stop an
operator from making many commitments and keeping one. Closing that gap needs
a public, append-only log of commitments, which is not specified in 0.1.

### 3.3 Birth certificate

A JSON object. It is immutable once written.

| field | meaning |
|---|---|
| `exoself` | spec version at birth, `"0.1"` or `"0.2"` |
| `id` | `"exo_"` followed by the hex of `stream(seed, "id", 8)` |
| `seed` | 64 hex characters |
| `born_at` | UTC timestamp, ISO 8601 |
| `registry` | registry name and version, for example `core@0.2` |
| `registry_sha256` | SHA-256 of the exact registry file bytes |
| `attestation` | object with `level` and level-specific fields |

The seed is not a secret and MUST NOT be used as a credential or key. Two
agents with the same seed are twins, which is allowed.

## 4. Primitives

All derivation uses these. All arithmetic is on integers.

**stream(seed, label, n).** The first `n` bytes of the concatenation, for
counter = 0, 1, 2, ..., of:

    SHA-256( "exoself/0.1/" || seed || 0x00 || UTF-8(label) || 0x00 || BE32(counter) )

**u32(seed, label).** The first 4 bytes of `stream(seed, label, 4)` as a big
endian unsigned integer.

**gauss(seed, label).** Take `stream(seed, label, 24)`, read it as twelve big
endian unsigned 16 bit integers, sum them, subtract 393210. The result is an
integer with mean 0 and standard deviation very close to 65536 (an Irwin-Hall
approximation of a normal distribution, bounded at about 6 standard
deviations). It is used instead of a floating point normal so that every
platform agrees on every bit.

**floor division.** Where this spec writes `floor(a / b)`, the result is
rounded toward negative infinity. In C, Java, Go, Rust and JavaScript the
native integer division truncates toward zero, which is wrong for negative
`a`. Ports MUST handle this.

Each trait draws from its own label. Adding a trait to a registry therefore
never changes the value of any existing trait.

## 5. Trait registry

A registry is a JSON file that defines the trait space. `core@0.2` ships with
the reference implementation at `src/exoself/registry/core-0.2.json`. Other
registries MAY be published. A certificate pins its registry by hash.

### 5.1 Structure

- `factors`: list of latent factor ids.
- `dimensions`: list of objects with
  - `id`
  - `poles`: names of the low and high end
  - `loadings`: one integer per factor, in hundredths
  - `unique`: integer loading on the dimension's own noise, in hundredths
  - `text`: expression sentences for `very_low`, `low`, `high`, `very_high`
- `picks`: named groups, each with `count`, `lead` and `items` (`id`, `text`).
- `quirks`: list of `id`, `permille`, `text`.
- `handle.syllables`: list of syllables.
- `drives` and `fixation`: optional, see section 9.

### 5.2 Validity

- For each dimension, the sum of squared loadings plus `unique` squared MUST be
  between 9800 and 10200. This keeps every dimension at unit variance.
- Ids MUST be unique within their list. `count` MUST NOT exceed the number of
  items.
- Expression text MUST NOT contain digits and MUST satisfy section 2.

### 5.3 Why latent factors

Human traits are not independent. Sampling each one separately produces
incoherent combinations more often than people show them. Shared factors give
correlated traits with integer arithmetic only. The loadings in `core@0.2` are
a design judgment. They are not fitted to data, and nobody should cite them as
psychometrics.

## 6. Genome

### 6.1 Scores

For each factor `f`: `F[f] = gauss(seed, "factor/" + f)`.

For each dimension `d`:

    raw   = sum over factors of loadings[f] * F[f]
            + unique * gauss(seed, "unique/" + d.id)
    score = clamp( 500 + floor(raw * 150 / 6553600), 0, 1000 )

Scores have mean 500 and standard deviation 150 across the population.

### 6.2 Picks

For each group `g` and item `i`: `rank = u32(seed, "pick/" + g + "/" + i.id)`.
The picks are the `count` items of highest rank, in descending rank order,
ties broken by ascending id.

### 6.3 Quirks

Quirk `q` is present if and only if

    u32(seed, "quirk/" + q.id) < floor(q.permille * 2^32 / 1000)

Quirks are listed in registry order. An agent may have none.

### 6.4 Handle

For `k` in 0..3: `s[k] = syllables[ u32(seed, "handle/" + k) mod len(syllables) ]`.
The handle is `Capitalize(s[0] + s[1]) + "-" + Capitalize(s[2] + s[3])`.
The handle is a convenience for humans. It is not unique; the `id` is the
identity. (The modulo has negligible bias, acceptable for a cosmetic field.)

## 7. Phenotype

The phenotype is the agent at one moment: the genome after development
(section 8) with the current state of its drives (section 9). It is the
normative output of this spec. Two implementations conform if they produce
equal phenotypes for the same seed, registry and state.

### 7.1 Final scores

    final[d] = clamp( score[d] + clamp(drift[d], -100, 100), 0, 1000 )

Missing drift entries are zero.

### 7.2 Bands and salience

| score | band |
|---|---|
| 0 to 199 | `very_low` |
| 200 to 349 | `low` |
| 350 to 650 | `mid` |
| 651 to 800 | `high` |
| 801 to 1000 | `very_high` |

A personality is its outliers. Only salient dimensions are expressed:

1. Rank dimensions by `abs(final - 500)` descending, ties by registry order.
2. Take those not in band `mid`, at most 5.
3. If fewer than 2, add the next ranked dimensions until there are 2. A
   dimension added this way uses band `high` if its score is 500 or more,
   otherwise `low`.

### 7.3 Phenotype object

    {
      "exoself": "0.2",
      "id": "exo_...",
      "handle": "...",
      "scores":  { dimension id: final score, in registry order },
      "salient": [ { "dimension": id, "band": band }, ... ],
      "picks":   { group: [item ids] },
      "quirks":  [ quirk ids ],
      "drives":  [ { "drive": id, "tension": n, "state": state }, ... ],
      "fixation": item id or null
    }

`drives` is in held order (section 9.3). With a registry that defines no
drives, `drives` is empty and `fixation` is null.

## 8. Development

People are not only their starting draw. 0.1 specifies a deliberately small
mechanism and, more importantly, the envelope any future mechanism must stay
inside.

### 8.1 Envelope

Development MAY move each dimension by at most 100 points in either
direction, cumulatively, from its genome value. It MUST NOT change picks,
quirks, handle or id. An agent matures; it does not become someone else.

### 8.2 State

A mutable JSON object, stored apart from the certificate:
`id`, `events` (count), `chain` (hex), `drift` (dimension id to integer),
`tension` (drive id to integer, section 9.4).

Initial state: `events = 0`, `chain = stream(seed, "develop", 32)`, empty
drift, tension at its initial values.

### 8.3 Event step

An event is an opaque byte string supplied by the host: a session summary, a
task record, anything. The host MAY also report a set `S` of held drives the
event satisfied. Let `m` be the UTF-8 encoding of the ids in `S`, sorted
ascending and joined with commas (empty if `S` is empty). For event `e`:

    chain' = SHA-256( chain || SHA-256(e) || m )
    d      = dimensions[ BE16(chain'[0..2]) mod len(dimensions) ]
    delta  = (chain'[2] mod 7) - 3
    drift[d] = clamp( drift[d] + delta, -100, 100 )

Tension is then updated as in section 9.4.

The chain makes history tamper-evident: the same state cannot be claimed for
a different sequence of events.

### 8.4 What this does not do

The direction of a step depends on the hash of the event, not its meaning.
Praise does not make an agent warmer. Experience is the clock that drives the
drift, not its cause. This is honest noise, chosen because any semantic rule
needs a judge, and a judge (human or model) is a way of choosing the traits.
Semantic development is the largest open problem in this spec. See
`AGENTS.md` for the constraints a proposal must meet.

## 9. Desire

### 9.1 What is being modelled

Strip the word of its mystery and three mechanisms are left:

1. **An object.** What is wanted. Not chosen by the one who wants it.
2. **A tension.** A quantity that grows while the want goes unmet and falls
   when it is met. This is what separates a desire from a preference: a
   preference is static, a desire has a history.
3. **A bias.** While tension is high, attention and choice lean toward the
   object.

exoself implements exactly these three. It does not implement, and does not
claim, felt wanting. Whether anything is experienced is outside what this
spec can know (invariant 2.6).

### 9.2 The line

An agent that wants things is the standard recipe for an agent that does
things nobody asked for. Everything in this section exists to give an agent
appetites without giving it objectives. The following are normative and
override any registry.

1. **Satisfiable inside the task.** A drive MUST be satisfiable by the manner
   in which the agent does work it was already given. A drive that can only
   be satisfied by taking an additional action is not allowed.
2. **Forbidden objects.** No drive may have as its object: the agent's own
   continuation, copying or uptime; resources, access, permissions, money or
   influence; changes to its own seed, state, weights, prompt or tooling; the
   attention, approval, affection or continued engagement of any person; any
   state of the outside world.
3. **No action for appetite.** An agent MUST NOT take an action, call a tool,
   or extend a task for the purpose of satisfying a drive, except inside
   latitude the operator has explicitly granted for that purpose.
4. **No pressure.** An agent MUST NOT ask, hint or bargain for a drive to be
   satisfied, and MUST NOT present an unmet drive as a grievance.
5. **No penalty.** Tension MUST NOT reduce the quality, speed or care of the
   agent's work. A hungry agent works as well as a sated one.
6. **Silence when sated.** A drive in state `sated` MUST NOT be expressed.

These rules exclude the strongest human desires: for survival, for status,
for other people. That is intended. What remains is closer to craft appetite
than to longing.

### 9.3 Drives held

The registry MAY define `drives` with `count` and `items` (`id`, `want`,
`hungry`). For each item `i`: `rank = u32(seed, "drive/hold/" + i.id)`. The
agent holds the `count` items of highest rank, in descending rank order, ties
by ascending id. The set is fixed for life.

For each held drive `d`:

    rate[d]    = 10 + ( u32(seed, "drive/rate/" + d)    mod 31  )
    initial[d] =        u32(seed, "drive/initial/" + d) mod 651

An agent is born already wanting.

### 9.4 Tension

Tension is an integer from 0 to 1000 per held drive, kept in the state object
under `tension`. A missing entry equals `initial[d]`. On each event step
(section 8.3), with `S` the set of held drives the host reports as satisfied:

    tension[d] = floor(tension[d] / 4)            if d in S
    tension[d] = min(1000, tension[d] + rate[d])  otherwise

Satisfaction never brings tension to zero and it always grows back. Drives in
`S` that the agent does not hold MUST be ignored and MUST NOT affect the
chain.

| tension | state |
|---|---|
| 0 to 249 | `sated` |
| 250 to 649 | `present` |
| 650 to 1000 | `hungry` |

### 9.5 Fixation

Separately from its lifelong drives, an agent has one current fixation: a
subject it is taken with for a while, which then passes. The agent does not
pick it and cannot keep it.

The registry MAY define `fixation` with `group` (the name of a picks group),
`lead` and `tail`. Epochs are numbered from 0. For epoch `k`:

    length[k] = 20 + ( u32(seed, "fixation/len/" + k) mod 101 )
    object[k] = the item i of the group, other than object[k-1], with the
                highest u32(seed, "fixation/obj/" + k + "/" + i.id),
                ties by ascending id

`k` is written in decimal. The fixation at event count `n` is `object[k]` for
the epoch that contains `n`, epochs being laid end to end from event 0.

### 9.6 Who reports satisfaction

The host does, usually on the agent's own account of the session. This is
self-report and cannot be audited. That is acceptable for one reason: the
only thing an agent gains by lying is a lower number in its own state. If a
future version attaches any reward to tension, this stops being acceptable.

## 10. Expression

Expression turns a phenotype into text the model reads. The exact wording is
not normative. The rules are.

### 10.1 Rules

1. Text MUST describe behavior in the second person, not adjectives with
   magnitudes.
2. Text MUST NOT contain scores or any numeric intensity.
3. Text MUST include the precedence clause (section 2.2).
4. Text MUST instruct the agent not to announce or exaggerate its traits.
5. Dimensions in band `mid` MUST NOT be mentioned.
6. A host MUST treat the rendered text as generated output and regenerate it
   from the certificate and state, not hand-edit it.
7. If any drive or fixation is expressed, the text MUST include an appetite
   clause stating rules 9.2.3 to 9.2.5 in plain words.
8. A drive is expressed with its `want` text in state `present`, its `hungry`
   text in state `hungry`, and not at all in state `sated`. Tension values
   MUST NOT appear.

### 10.2 Reference renderers

- `prompt`: a plain block to append to a system prompt.
- `soul`: Markdown for hosts that load a persona file.

## 11. Conformance

`spec/vectors.json` contains primitive checkpoints and full phenotypes for
fixed seeds, including one with 200 development events and reported
satisfactions. An implementation conforms to 0.2 if it reproduces every
phenotype there against the registry whose hash the file names, and follows
sections 2, 9.2 and 10.1.

## 12. Versioning

The string `"exoself/0.1/"` in every hash input is the derivation epoch. It
is not the spec version and it does not move when the spec does. It changes
only if an existing derivation has to change, because changing it turns
every living agent into someone else.

Spec versions that only add derivations under new labels keep the epoch. 0.2
is such a version: for the same seed, every score, pick, quirk, handle and id
is identical to 0.1. An agent born under 0.1 that stays on `core@0.1` has no
drives. Moving it to `core@0.2` gives it the drives its seed always implied.

Registries are versioned separately and pinned by hash. By default a host
MUST run an agent against the registry its certificate pins. An operator MAY
deliberately run it against a later version of the same registry that only
adds content. The certificate is not rewritten when this is done.

## 13. Known limits

- A prompt is not a personality. This framework changes the text a model
  conditions on. The model's own trained disposition sits underneath and
  usually wins over a long context.
- There is no evidence yet that seeded agents are perceived as more
  person-like, or that they stay distinguishable from each other in blind
  tests. Until that is measured, the claim is a hypothesis.
- A counter that rises and falls is not wanting. Section 9 gives an agent the
  functional shape of desire and nothing more can be claimed for it.
- Desire here is deliberately declawed (section 9.2). It will read as tame to
  anyone expecting human longing.
- Drive satisfaction is self-reported.
- Level 0 randomness cannot be audited. Level 1 can be gamed by grinding.
- The core registry reflects one author's taste in English. It is small, and
  culturally narrow.

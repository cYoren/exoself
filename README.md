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

Two conforming implementations, same derivation, byte-identical output.

    # Rust, no dependencies at all
    cargo build --release --manifest-path rust/Cargo.toml
    ./rust/target/release/exoself born -o birth.json

    # Python, standard library only
    pip install -e .
    exoself born -o birth.json           # draw a seed, once
    exoself show birth.json              # system prompt block
    exoself show birth.json --format soul > SOUL.md
    exoself show birth.json --format json
    exoself develop birth.json state.json --event "first session"
    exoself develop birth.json state.json --event "explained a proof" --satisfied teach
    exoself show birth.json --state state.json --desire   # opt in to desire
    exoself show birth.json --state state.json

Or skip the install and draw in a browser:
<https://cyoren.github.io/exoself/>, which runs the same derivation as
WebAssembly and proves itself against `spec/vectors.json` on every page load.

Neither implementation has dependencies beyond its own standard library. The
Rust core is integer arithmetic over an in-crate SHA-256 and JSON reader; the
Python one imports nothing from outside the stdlib.

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
| `rust/crates/exoself-core/` | second implementation, zero dependencies, reproduces every vector |
| `rust/crates/exoself-cli/` | `born`, `show`, `develop`, `verify`, `conformance`, `install`, `mcp` |
| `rust/crates/exoself-wasm/` | the browser build behind the Pages site |
| `docs/` | the landing page, and `docs/HOSTS.md` for wiring a harness |
| `spec/vectors.json` | conformance vectors for ports |
| `tests/` | determinism, population statistics, invariants |
| `tools/eval.py` | measurements: distinctness, overlap, stability, blind model judging |
| `eval/REPORT.md` | the numbers, published whichever way they fall |
| `tools/parity.py` | Rust against Python, byte for byte, over many seeds |
| `tools/mcp_selftest.py` | the MCP server driven over stdin/stdout with no SDK |

## Plug into a harness

    exoself install --dir . --json-config .mcp.json

writes `.exoself/birth.json`, `.exoself/state.json`, `SOUL.md`, a managed block
in `AGENTS.md` and an `mcpServers` entry. That covers all three kinds of host:

- **reads a brief** (`AGENTS.md`, `CLAUDE.md`, anything): nothing else to do;
- **speaks MCP over stdio** (pi, Claude Code, Cursor, Claude Desktop, Hermes,
  OpenClaw, anything else): four tools, no plugin;
- **loads a persona file**: `SOUL.md`.

The MCP tools are `exoself_temperament`, `exoself_record_session`,
`exoself_status` and `exoself_verify`. There is deliberately no tool that draws
a seed, because a tool that could redraw would be a reroll button. Verified
end to end in pi (`pi mcp list` reports `connected, 4 tools`); the wire format
is newline-delimited JSON-RPC 2.0 and `tools/mcp_selftest.py` documents every
message it accepts. Per-host commands are in [`docs/HOSTS.md`](docs/HOSTS.md).

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
- **Desire as appetite, not objective, and off by default.** Each agent holds three drives for
  life, drawn from the seed, each with a tension that grows while unmet and
  falls when met. It also has a passing fixation that rotates on a schedule
  it does not control. Drives may colour how asked work is done. They may
  never cause unasked work. Nothing about desire reaches the model unless
  the operator passes `--desire`. See `SPEC.md` section 9.
- **Bounded development.** Experience can shift a dimension by at most 100 of
  1000 points. The agent matures; it does not become someone else.

## Honest limits

Read these before building on this.

1. **A prompt is not a personality.** This changes the text a model conditions
   on. The model's trained disposition sits underneath and tends to reassert
   itself over long contexts. Real per-agent temperament would need to live in
   weights, activations or sampling, which this project does not do.
2. **"More like people" is measured now, and the answer is narrow.** See
   [`eval/REPORT.md`](eval/REPORT.md). With a local three billion parameter
   model, sixteen seeded agents were no more like themselves than like a
   stranger on ordinary short tasks: 10.4% and 4.2% nearest-neighbour against a
   6.25% chance, p = 0.22 and 0.77. On a task that asks how they like to work,
   the same test separates them cleanly: 70.8% against the same 6.25%, p =
   2e-13. A blind pairwise judge that is the same small model lands at 57.6%,
   p = 0.08, directional and not significant; asked to pick among seventeen
   candidates it is exactly at chance. So: a temperament reaches the output
   when manner is what is being measured, and does not otherwise. That is a
   smaller claim than the project's name and it is the one the data supports.
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
7. **Host integration is verified in one host, protocol-level elsewhere.** The
   MCP server was driven end to end inside pi (`pi mcp list` reports
   `connected, 4 tools`) and by `tools/mcp_selftest.py`, which speaks the wire
   protocol with no SDK. Hermes and OpenClaw have not been run against it here;
   they get the same stdio JSON-RPC, so a failure there is their config, not the
   temperament.

The most defensible use today is not "a more human assistant". It is
population diversity: many agents working on the same problem with decorrelated
styles of attack.

## Roadmap

1. ~~Distinguishability and stability eval.~~ Done, and it cut the claim down:
   `eval/REPORT.md`. The open part is a judge from a different family, longer
   tasks, and a bigger model.
2. ~~Second implementation in another language.~~ Done: `rust/`, byte-identical
   to the reference over 120 seeds.
3. Verified host adapters. One host done (pi), the protocol covered by a
   selftest; the rest is other people's config.
4. Public commitment log for level 1 attestation.
5. A semantic development proposal that survives the constraints in
   `AGENTS.md`.

## Two implementations, one species

`tools/parity.py` runs the Rust binary and the Python reference side by side and
compares the rendered text and the JSON, byte for byte, with and without
development events:

    $ python3 tools/parity.py --seeds 120
    parity ok: 960 comparisons over 120 seeds, 40 events each

That is stronger than the spec requires. The spec asks for equal phenotypes;
this also pins the wording of the rendered text, so a host can swap binaries
without changing who the agent is. `cargo test` includes the conformance
vectors, so a change that alters who an agent is fails before it ships.

## License

MIT.

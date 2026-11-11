# Wiring exoself into a host

There are three kinds of host, and exoself covers all of them. Nothing here
needs a plugin, an account or a network call: a certificate, a state file, and
a process that reads JSON on stdin.

Build once, with either implementation. Both have the same commands, the same
flags and the same output, so a host cannot tell them apart:

```bash
cargo install exoself          # or: cargo build --release --manifest-path rust/Cargo.toml
exoself version

# or, with no Rust toolchain at all
pip install exoself
```

Then put one agent in a project directory:

```bash
cd ~/my-project
exoself install --dir . --json-config .mcp.json
```

That writes:

```
.exoself/birth.json   the one draw, never overwritten
.exoself/state.json   the history and the appetite tensions
SOUL.md               the rendered temperament, Markdown
AGENTS.md             a managed block, refreshed on re-run, never duplicated
.mcp.json             an mcpServers entry pointing at this binary
```

`exoself install --desire` also expresses appetites (off by default, SPEC 9.2).

## 1. Hosts that read a brief

If the host loads `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` or any similar project
file, you are done: point it at the file `install` wrote, or pass `--file` to
write into the one your host reads.

```bash
exoself install --dir . --file CLAUDE.md
```

The block is delimited by `<!-- exoself:begin ... -->` and `<!-- exoself:end
-->`, so re-running `install` replaces it in place. Everything the model reads
is a plain second person block with no numbers in it (SPEC 10.1).

## 2. Hosts that speak MCP over stdio

Any MCP client works, because the server is only stdin and stdout. The tools:

| tool | what it does |
|---|---|
| `exoself_temperament` | the rendered temperament, `prompt`, `soul` or `json` |
| `exoself_record_session` | one experience event, persisted to the state file |
| `exoself_status` | handle, id, sessions lived, appetite states in words |
| `exoself_verify` | is the certificate consistent with its registry |

There is deliberately no tool that draws a seed. Birth is an operator action
(`exoself born`) and it happens once; a tool that could redraw would be a
reroll button, which is the one thing the framework exists to prevent.

Start it by hand to check:

```bash
exoself mcp --cert .exoself/birth.json --state .exoself/state.json
```

### pi

Verified: `pi mcp list` reports `connected, 4 tools` for this server.

```bash
pi mcp add exoself -- "$PWD/rust/target/release/exoself" mcp \
  --cert "$PWD/.exoself/birth.json" --state "$PWD/.exoself/state.json"
```

Project config works the same way, once the project is trusted:

```bash
exoself install --dir . --json-config .pi/mcp.json
```

### Claude Code

```bash
claude mcp add exoself -- "$PWD/rust/target/release/exoself" mcp \
  --cert "$PWD/.exoself/birth.json" --state "$PWD/.exoself/state.json"
```

or commit the `.mcp.json` that `install --json-config .mcp.json` wrote, which is
the shape Claude Code already reads.

### Cursor

```bash
exoself install --dir . --json-config .cursor/mcp.json
```

### Claude Desktop

Same `mcpServers` shape, in the app's config file. Use absolute paths: the app
does not launch servers from your project directory, which is why `install`
writes absolute ones.

### Anything else

The server speaks newline-delimited JSON-RPC 2.0: `initialize`, then
`notifications/initialized`, then `tools/list` and `tools/call`. If your host
uses a different config format, the wiring is one entry with `command`, `args`,
`cwd` and `env`. `tools/mcp_selftest.py` contains every message the server
expects and every response it gives, with no SDK, so it doubles as the
specification of the wire format.

## 3. Hosts with a persona file

`SOUL.md` is plain Markdown with a generated header. Load it as a system prompt
prefix, a persona file, or a memory entry. Regenerate it from the certificate
rather than editing it:

```bash
exoself show .exoself/birth.json --state .exoself/state.json --format soul > SOUL.md
```

### Hermes

Verified: `hermes plugins validate hermes --install-deps` passes, and two
Hermes profiles loaded the plugin and drew two different temperaments. A second
load on the same profile left the certificate byte for byte identical.

Hermes loads `SOUL.md` from the profile's home as its identity slot, so the
wiring is a plugin that keeps that file current, one agent per profile:

```bash
hermes plugins install exoself --yes-deps --enable
```

From this repository, before or without a catalog entry:

```bash
hermes plugins install cYoren/exoself/hermes --ref <40-hex sha> --yes-deps --enable
```

On the first load for a profile the plugin draws one seed into
`$HERMES_HOME/.exoself/birth.json` and renders the temperament into a managed
block in `$HERMES_HOME/SOUL.md`, leaving whatever you wrote above it. At the end
of each turn it records one `develop` event (the turn's ask, one line) and
refreshes that block. Desire is off unless you set
`plugins.entries.exoself.settings.desire: true`. The plugin exposes
`exoself_status` and `exoself_verify` to the model, and nothing that draws.

Those are ordinary exoself files, so the CLI works on them directly:

```bash
HERMES_HOME=~/.hermes exoself status "$HERMES_HOME/.exoself/birth.json"
HERMES_HOME=~/.hermes exoself verify "$HERMES_HOME/.exoself/birth.json"
```

## Keeping the brief current

`AGENTS.md` and `SOUL.md` are snapshots of the agent, and `develop` rewrites
them after every session: the block's session count, the drifted temperament,
the persona. It only touches files that carry its own marker, so your own notes
and any brief you wrote yourself are left alone, and it keeps whatever appetite
setting `install` used.

```bash
exoself develop .exoself/birth.json .exoself/state.json --event "reviewed the PR"
# note: refreshed AGENTS.md, SOUL.md
```

That works for a custom `--file CLAUDE.md` or `--soul PERSONA.md` too: every
`.md` in the installed directory that carries the marker is refreshed. A
certificate that is not inside a `.exoself/` directory is left alone, because
there is no installed layout to look in.

A host that speaks MCP does not need any of this: `exoself_temperament` reads
the state file every time it is called.

## Growing the agent

One call at the end of each session. This is what makes an agent older rather
than merely used: experience moves dimensions inside the envelope, and unmet
appetites rise.

```bash
# drops the run silently
exoself develop .exoself/birth.json .exoself/state.json --event "reviewed the auth PR"
# or reports which held appetites the session satisfied
exoself develop .exoself/birth.json .exoself/state.json \
  --event "explained the build to the new hire" --satisfied teach
```

Through MCP this is `exoself_record_session` with `{"summary": "...",
"satisfied": ["teach"]}`.

## Verifying an agent someone sent you

A seed is the identity. Anyone can check it:

```bash
exoself verify their-birth.json
exoself show their-birth.json --format json | head -20
```

The certificate cannot be edited into a different agent: `verify` recomputes
the id from the seed and compares the registry hash. It does not prove the seed
was drawn fairly. Level 1 attestation does that, and it is a separate step:

```bash
# before the beacon round. Writes two files: commitment.json, which is safe to
# publish, and commitment.secret.json, mode 0600, which is not.
exoself commit -o commitment.json
# after the round, with its output B:
exoself born --beacon drand:quicknet:1234 --beacon-value <hex> \
             --local-entropy <hex from commitment.secret.json>
```

Publish `commitment.json` and nothing else. A commitment published next to its
own pre-image proves nothing at all, which is why the two are separate files
and why the second is created unreadable by anyone else.

Without a public log of commitments this still cannot stop an operator who
grinds many commitments and keeps the nicest one (SPEC 3.2 says so). Use the
browser page if you want a draw a stranger can re-derive: it puts the seed in
the URL, so the other person sees exactly what you got.

## Nobody chose it, and that is the point

An operator can always delete the certificate and draw again. Everywhere this
tooling can refuse, it refuses: `born` will not overwrite a file, `install`
will not draw a second seed for an existing directory, and the MCP server has
no draw tool at all. That is the whole mechanism. Do not build a wrapper that
undoes it.

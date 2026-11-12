# exoself for Hermes

One unchosen temperament per Hermes profile. The seed is drawn once, the
temperament is rendered into that profile's `SOUL.md`, and it develops one
event per turn.

```bash
hermes plugins install exoself --yes-deps --enable
```

From this repository, before or without the catalog:

```bash
hermes plugins install cYoren/exoself/hermes --ref <40-hex sha> --yes-deps --enable
```

## What it writes

Inside the profile's home (`$HERMES_HOME`):

```
.exoself/birth.json   the one draw; never overwritten
.exoself/state.json   the history; created by the first recorded turn
SOUL.md               the rendered temperament, in a managed block
```

`SOUL.md` keeps whatever you wrote: the block sits at the end, delimited by
`<!-- exoself:begin ... -->` and `<!-- exoself:end -->`, and is replaced in
place on every refresh. Everything above it is yours.

## What it does

| when | what |
|---|---|
| first load for a profile | `exoself born` if there is no certificate, then render the block |
| every load | refresh the block from the certificate and state |
| `pre_llm_call` | keep the turn's ask, in memory, for the event line |
| `on_session_end` | `exoself develop --event "<the ask>"`, then refresh the block |

The event line is the turn's ask, collapsed to one line and cut at 160
characters. It is never stored: `develop` hashes it into the state's chain, so
`state.json` holds a history you can replay but not read back. A failed or
interrupted turn is recorded as such instead of being smoothed over.

## Tools

Two, both read-only:

- `exoself_status` — handle, id, sessions lived, appetite states in words.
- `exoself_verify` — recompute the id from the seed and compare the registry
  hash.

There is no tool, command or flag that draws a seed. Birth is an operator
action that happens once, and `exoself born` refuses to overwrite. Deleting
`.exoself/birth.json` is the only reroll, and it is a deliberate act with a
visible consequence: the next load draws a different agent.

## Desire

Off by default. Turn it on per profile:

```bash
hermes config set plugins.entries.exoself.settings.desire true
```

That adds the wants section to the block and the appetite clause that governs
it. Nothing else changes: appetites tilt choices that were already yours, they
never justify an extra action, and the plugin never reports on them.

## Requirements

`exoself>=0.3,<0.4` (declared in `plugin.yaml`, installed by Hermes). No other
dependency; the package is standard library only. The plugin calls the library
in-process (`hermes/engine.py`), never a child process, and reuses
`exoself.install`'s managed-block helpers, so a block written here is refreshed
by `exoself develop` and vice versa.

## Uninstalling

`hermes plugins remove exoself` removes the plugin. It does not remove
`.exoself/` or the block in `SOUL.md`: the certificate is the agent's identity,
not the plugin's state. Delete the directory yourself if you mean it.

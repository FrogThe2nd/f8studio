#### When to Use

- Use `Sequence Player` when a prepared sequence should play back over time and expose current value/index state.
- It is a good fit for reproducible demos and scripted motion passages.

#### Common Wiring Patterns

- Feed it a sequence payload, then branch `value` into mapping or device output nodes and monitor `index`/`done` for control logic.
- Pair it with `Playback Sync` when the rest of the graph needs to follow the same timeline.

#### Pitfalls / Gotchas

- Sequence schema errors can look like timing problems if the payload contract is not validated first.
- Keep timeline ownership clear so the graph does not fight between live and prerecorded sources.


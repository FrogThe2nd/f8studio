#### When to Use

- Use `Program Wave` when a graph should emit or shape a reusable wave/program payload rather than a single scalar.
- It is useful for authoring richer motion sequences upstream of playback or device formatting nodes.

#### Common Wiring Patterns

- Pair it with `Tick`, `Phase`, or `Sequence Player`, then inspect outputs before converting them into `TCode`.
- Keep it upstream from protocol-specific nodes so one program can feed multiple targets.

#### Pitfalls / Gotchas

- Program-oriented nodes depend on consistent payload shape; validate that contract early.
- If the graph only needs a single scalar, this node may be more abstraction than you need.


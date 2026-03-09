#### When to Use

- Use `Tick` when the graph needs a simple periodic exec clock.
- It is the usual root node for deterministic update loops in `f8.pyengine`.

#### Common Wiring Patterns

- Drive `Sequence` when several branches must run in a predictable order each tick.
- Keep `tickMs` aligned with downstream device cadence such as `TCode.intervalMs`.

#### Pitfalls / Gotchas

- Very small intervals can make the whole graph look unstable when the real problem is scheduling load.
- If downstream nodes are not exec-driven, adding more tick roots will not help.


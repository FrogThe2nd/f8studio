#### When to Use

- Use `Range Map` when one numeric range must be clipped and remapped into another.
- It is the default scaling node before actuator-facing outputs.

#### Common Wiring Patterns

- Feed it cleaned scalar values, then send mapped output to `TCode`, `Serial Out`, or `WaveViz`.
- Tune `inMin/inMax` against real observed source values before finalizing `outMin/outMax`.

#### Pitfalls / Gotchas

- A bad input range makes every downstream node look wrong.
- Curve shaping is easier to evaluate visually than by guessing from device behavior alone.


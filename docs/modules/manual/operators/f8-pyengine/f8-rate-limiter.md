#### When to Use

- Use `Rate Limiter` when output should change no faster than a configured slope or step rate.
- It is useful for making actuator commands safer and less jumpy.

#### Common Wiring Patterns

- Place it late in the chain, after mapping and smoothing but before hardware/protocol output.
- Compare limited vs unlimited branches with `WaveViz` when setting release-safe values.

#### Pitfalls / Gotchas

- If placed too early, it can distort the semantics of the whole control signal.
- Aggressive limiting can make the graph feel unresponsive even when upstream timing is correct.


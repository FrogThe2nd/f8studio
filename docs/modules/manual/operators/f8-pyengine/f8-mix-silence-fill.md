#### When to Use

- Use `Mix (Silence Fill)` when multiple sources should be combined while missing inputs are treated as silence rather than failure.
- It is useful in audio/control graphs that must remain continuous even when one branch drops out.

#### Common Wiring Patterns

- Mix upstream branches before final smoothing or mapping so you can inspect one combined signal.
- Keep `WaveViz` after the mixer while balancing branch contribution.

#### Pitfalls / Gotchas

- Silence fill can hide an upstream outage if there is no separate health visualization.
- Mixing too early can make debugging branch-specific issues much harder.


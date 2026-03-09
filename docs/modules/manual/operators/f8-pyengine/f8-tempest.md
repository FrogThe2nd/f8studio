#### When to Use

- Use `Tempest` when you want a phase-driven waveform with more character than plain cosine.
- It fits motion patterns that need controllable curvature and asymmetry.

#### Common Wiring Patterns

- Feed it from `Phase`, then normalize or map the result before hardware output.
- Compare it against `Cosine` in parallel with `WaveViz` before choosing a release preset.

#### Pitfalls / Gotchas

- `eccentric` can make the curve feel broken if you have not first validated the base phase path.
- Treat it as a shaping stage, not as a substitute for final output range control.


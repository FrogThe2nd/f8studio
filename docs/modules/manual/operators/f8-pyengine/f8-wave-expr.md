#### When to Use

- Use `Wave Expr` when you want a procedural looping waveform defined by an expression instead of hand-authored points.
- It is ideal for reusable modulation shapes that depend on `t` plus a few numeric parameters exposed as node state.

#### Common Wiring Patterns

- Drive the `t` input from a clock, phase, or playback timeline, then feed the `value` output into movement, envelope, or device-control operators.
- Expose a small set of numeric state fields as variables so the same expression can be tuned interactively without rewriting the template.

#### Pitfalls / Gotchas

- Reserved names in the expression language can collide with custom variable names, so parameter naming matters.
- `maxT` defines the loop period; if your upstream time source and preview assumptions disagree about period or range, the waveform can look correct in isolation but feel wrong in the full graph.

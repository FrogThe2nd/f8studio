## When to Use

- Use `f8.dl.classifier` when each frame should map to one label or class distribution.
- It is best for scene labels, coarse state estimation, or discrete control cues.

## Common Wiring Patterns

- Feed it from `f8.implayer` or `f8.screencap`, then inspect outputs with `TextViz`, `Python Expr`, or downstream state mappings.
- Keep a visualization or logging branch attached while tuning thresholds and class interpretation.

## Pitfalls / Gotchas

- Wrong model assets or label assumptions can look like logic bugs in downstream nodes.
- Classification outputs need explicit business rules before they drive devices or actuator logic.


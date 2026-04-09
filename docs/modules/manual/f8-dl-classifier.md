## When to Use

- Use `f8.dl.classifier` when each frame should map to one label or class distribution.
- It performs whole-image classification on video frames provided via Shared Memory and outputs a class distribution or top-K predicted labels.
- It is best for scene labels, coarse state estimation, or discrete control cues.

## Common Wiring Patterns

- Feed it from `f8.implayer` or `f8.screencap`, then inspect outputs with `Text Viz`, `Python Expr`, or downstream state mappings to drive application logic.
- Keep a visualization or logging branch attached while tuning thresholds and class interpretation.

## Pitfalls / Gotchas

- Wrong model assets or label assumptions can look like logic bugs in downstream nodes; verify the loaded model in the properties.
- Classification outputs need explicit business rules (hysteresis, thresholds) before they drive physical devices or actuator logic.

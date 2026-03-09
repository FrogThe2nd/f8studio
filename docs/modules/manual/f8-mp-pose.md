## When to Use

- Use `f8.mp.pose` when a graph needs lightweight body pose estimation from a video stream.
- It is a practical choice for skeleton-driven demos and authoring workflows before adopting heavier custom models.

## Common Wiring Patterns

- Feed it from `f8.implayer` or `f8.screencap`, then inspect outputs with `f8.viz.three_d` or `f8.pyengine` bone-processing operators.
- Keep the original video path visible during tuning so pose failures are easier to diagnose.

## Pitfalls / Gotchas

- Pose quality is strongly tied to framing, subject scale, and source quality.
- Downstream graphs should not assume a stable skeleton until the pose stream itself is visually validated.


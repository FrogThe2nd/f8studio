## When to Use

- Use `f8.dl.optflow` when learned flow quality is preferred over the classical CV implementation.
- It provides high-quality dense optical flow using the NeuFlowV2 architecture, which is often more robust to lighting changes and local occlusions.
- It is a strong option for motion-sensitive graphs when model-backed flow is already part of the deployment stack.

## Common Wiring Patterns

- Feed it from video producers, then inspect outputs through `f8.viz.video` (using flow visualization) or reduce them with `f8.cvkit.flowmetric`.
- Compare it against the CVKit flow path before committing a release pipeline to balance performance and quality.

## Pitfalls / Gotchas

- GPU/runtime availability matters for performance; validate packaging and device selection (`ortProvider`) early.
- Flow quality problems are often input-quality problems (blur, noise) rather than model bugs.

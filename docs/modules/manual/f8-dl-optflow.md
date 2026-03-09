## When to Use

- Use `f8.dl.optflow` when learned flow quality is preferred over the classical CV implementation.
- It is a strong option for motion-sensitive graphs when model-backed flow is already part of the deployment stack.

## Common Wiring Patterns

- Feed it from video producers, then inspect outputs through `f8.viz.video` or reduce them with `f8.cvkit.flowmetric`.
- Compare it against the CVKit flow path before committing a release pipeline.

## Pitfalls / Gotchas

- GPU/runtime availability matters; validate packaging and device selection early.
- Flow quality problems are often input-quality problems rather than model bugs.


## When to Use

- Use `f8.cvkit.videostab` to reduce camera shake before downstream analysis or viewing.
- It is helpful when input footage has obvious hand motion, screen wobble, or camera jitter.
- It is especially useful when later detection or tracking is sensitive to unstable imagery.

## Common Wiring Patterns

- A typical chain is `video source -> f8.cvkit.videostab -> other CV modules`.
- Keep an original-video bypass when comparing quality and tuning parameters.
- If stabilization is needed, it usually belongs early in the pipeline.

## Pitfalls / Gotchas

- Stabilization adds both compute cost and latency.
- Aggressive settings can create crop, warp, or "floating" artifacts.
- If the source is already stable enough, do not add this module just for appearance.

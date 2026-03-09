## When to Use

- Use `f8.dl.detector` when you need object detections with boxes or class-specific regions.
- It is the general DL detection path for scenes that are broader than human-only use cases.

## Common Wiring Patterns

- Feed it from a video producer, then inspect detections via `TextViz`, overlays, or handoff into `f8.pyengine` logic.
- Keep the raw video source available in parallel for side-by-side validation.

## Pitfalls / Gotchas

- Threshold tuning is meaningless until the correct model and input resolution are confirmed.
- Detection-heavy graphs can look sluggish if inference cost is ignored during release packaging.


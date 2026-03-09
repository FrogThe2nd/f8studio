## When to Use

- Use `f8.dl.humandetector` when the graph only cares about people and not general object classes.
- It is a cleaner release choice than a broader detector when the downstream logic is human-specific.

## Common Wiring Patterns

- Feed it from `f8.implayer` or `f8.screencap`, then branch detections to overlays, skeleton-related logic, or state summaries.
- Keep it paired with a visual validation branch during threshold tuning.

## Pitfalls / Gotchas

- Human-only detectors still depend on source framing and input scale; low-quality inputs reduce confidence quickly.
- Users often tune downstream logic before verifying that the detector itself sees the subject reliably.


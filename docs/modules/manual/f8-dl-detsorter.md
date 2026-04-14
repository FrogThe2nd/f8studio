## When to Use

- Use `f8.dl.detsorter` to turn raw frame-by-frame detections into more stable ranked or identity-like output.
- It is useful when the detector sees several candidates but the graph only wants one stable target or a more coherent ordering.
- It is often the next step when detector output still feels too noisy for control logic.

## Common Wiring Patterns

- A common chain is `f8.dl.detector -> f8.dl.detsorter -> downstream logic`.
- If a later stage depends on continuity, sorting before tracking can improve behavior.
- Compare raw detections and sorted output side by side while tuning.

## Pitfalls / Gotchas

- A sorter cannot fully recover from poor detector quality.
- Occlusion and frequent entry/exit events will still cause identity churn in hard scenes.
- Decide early whether you need "best current target" or "best continuity" because that affects downstream design.

## When to Use

- Use `f8.dl.detsorter` when detections already exist and you want to reorder them by a second signal such as saliency, motion, or another score-map SHM.
- It is a good fit for "pick the most interesting box first" pipelines where plain detector confidence is not the ranking you want downstream.

## Common Wiring Patterns

- Feed `detections` from `f8.dl.detector` or `f8.dl.humandetector`, point `scoreShmName` at a `scalar1_f32` or `flow2_f16` SHM source, then send the sorted `detections` payload to overlays, text inspection, or custom logic.
- Start with `scoreAggregation=mean` and `sortDirection=desc`, then add `clsWeights` only after the base score-map ranking behaves as expected.

## Pitfalls / Gotchas

- `f8.dl.detsorter` only reorders detections; it does not change each detection's original `score` field, so downstream logic must not assume the first item has the highest detector confidence.
- Ranking quality depends on score-map alignment. If the detection payload resolution differs from the SHM resolution, the service rescales boxes before scoring, so mismatched crops or stale SHM content can produce surprising orderings.

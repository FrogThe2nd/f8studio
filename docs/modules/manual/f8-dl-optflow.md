## When to Use

- Use `f8.dl.optflow` when you need higher-quality motion estimation than simpler flow approaches provide.
- It is most useful in scenes where motion vectors materially affect later logic and traditional flow is not good enough.
- Choose it when flow quality matters more than absolute runtime cost.

## Common Wiring Patterns

- Feed it from a video source and route the result into `f8.pyengine`, summary logic, or visualization.
- If `cvkit.denseoptflow` is already in use, compare both approaches in parallel before committing to the DL path.
- Reserve it for places where the graph actually benefits from better motion quality.

## Pitfalls / Gotchas

- DL flow tends to be significantly more compute-heavy.
- If the downstream logic only needs a rough activity score, this module may be overkill.
- Validate on known sample footage before dropping it into a large live graph.

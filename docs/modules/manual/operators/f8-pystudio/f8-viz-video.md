#### When to Use

- Use `Video Viz` when a graph needs a Studio-local preview of image or video SHM content plus related overlays.
- It is the primary way to sanity-check capture, preprocessing, and detection-style outputs while building a scenario.

#### Common Wiring Patterns

- Connect the same source that feeds CV, DL, or pose services so you can compare visual input against downstream results.
- Keep overlays and raw source views nearby when you are tuning thresholds, crops, or coordinate alignment.

#### Pitfalls / Gotchas

- A local preview can look healthy even when deployment-specific timing or SHM wiring differs elsewhere.
- If the wrong source branch is connected, you can waste time debugging overlays when the real issue is upstream video selection.

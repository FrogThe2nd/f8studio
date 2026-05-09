## When to Use

- Use `Video Viz` when a graph needs a Studio-local preview of Zenoh latest-frame video plus related overlays.
- It is the primary way to sanity-check capture, preprocessing, and detection-style outputs while building a scenario.
- It supports real-time rendering of bounding boxes, skeletons, and custom point sets on top of the video frames.

## Common Wiring Patterns

- **Sanity Check**: Connect the same typed `video` data source that feeds CV, DL, or pose services so you can compare visual input against downstream results.
- **Tuning Overlay**: Keep overlays and raw source views nearby when you are tuning thresholds, crops, or coordinate alignment for detection nodes.
- **Reference View**: Route the "stabilized" or "preprocessed" Zenoh video stream here to verify that your CV pipeline is receiving clean data.

## Pitfalls / Gotchas

- **Studio Local Only**: A local preview can look healthy even when deployment-specific timing or Zenoh endpoint configuration differs elsewhere (e.g., on a headless remote node).
- **Source Selection**: If the wrong source branch is connected, you can waste time debugging overlays when the real issue is an upstream data-port wiring mismatch.
- **Canvas Clutter**: Too many video previews in a single Studio tab can impact local UI performance; only keep the views you actively need.

## When to Use

- Use `f8.dl.detector` when you need object detections with boxes or class-specific regions.
- It provides general-purpose object detection using ONNX Runtime, consuming video frames from Shared Memory and outputting detection payloads (bounding boxes, scores, and classes).
- It is the general DL detection path for scenes that are broader than human-only use cases.

## Common Wiring Patterns

- Feed it from a video producer (e.g., `f8.implayer`), then inspect detections via `Text Viz`, overlays, or handoff into `f8.pyengine` logic for business rules.
- Keep the raw video source available in parallel for side-by-side validation during confidence threshold tuning.

## Pitfalls / Gotchas

- Threshold tuning is meaningless until the correct model and input resolution are confirmed.
- Detection-heavy graphs can look sluggish if inference cost is ignored during release packaging; monitor the monitor port for latency.

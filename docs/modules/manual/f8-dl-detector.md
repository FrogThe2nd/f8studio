## When to Use

- Use `f8.dl.detector` when you need bounding boxes, classes, and confidence scores for objects in the frame.
- It is the usual first step when the graph needs to know where targets are.
- Choose it when multiple candidate objects may appear and later logic needs structured detections.

## Common Wiring Patterns

- Typical inputs are `f8.implayer` and `f8.screencap`.
- Keep the original video visible during tuning so detection quality can be verified against the image.
- Detection output often continues into `f8.dl.detsorter`, `f8.cvkit.tracking`, or `f8.pyengine`.

## Pitfalls / Gotchas

- Do not tune thresholds before confirming that model selection, input sizing, and class mapping are correct.
- Slow performance can come from oversized input or deployment limits, not just from the model itself.
- If results fluctuate badly, inspect source image quality before over-tuning postprocessing.

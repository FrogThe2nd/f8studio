## When to Use

- Use `f8.cvkit.denseoptflow` when you need per-pixel motion vectors from a video stream.
- It is the classical CV path for motion-derived control and visual flow inspection.

## Common Wiring Patterns

- Feed video from `f8.implayer` or `f8.screencap`, then branch flow output to `f8.viz.video` overlays or `f8.cvkit.flowmetric`.
- Keep this service close to the producer so frame timing and resolution assumptions stay obvious.

## Pitfalls / Gotchas

- If the input SHM name is wrong, downstream flow consumers will look empty even though the graph compiles.
- Dense flow is sensitive to source quality, scale changes, and noisy screen capture inputs.


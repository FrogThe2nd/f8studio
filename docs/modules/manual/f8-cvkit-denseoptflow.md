## When to Use

- Use `f8.cvkit.denseoptflow` when you need per-pixel motion vectors to analyze how every part of a video frame is moving.
- It provides a "classical" Computer Vision approach for motion-derived control, visual flow inspection, and regional motion summaries.
- Ideal for general motion detection, background subtraction based on movement, or as a source for high-resolution flow metrics.

## Common Wiring Patterns

- **Motion to Metric**: Feed video from `f8.implayer` or `f8.screencap`, then connect the flow SHM output to `f8.cvkit.flowmetric` for scalar reduction (e.g., total motion magnitude).
- **Visualization**: Branch the flow output to `f8.viz.video` with an optical flow overlay to visually inspect the direction and intensity of motion.
- **Preprocessing Placement**: Keep this service as close as possible to the video producer to ensure the lowest latency and clear resolution assumptions.

## Pitfalls / Gotchas

- **Channel Mismatch**: If the input SHM name is incorrect, downstream flow consumers will silently receive no data. Always verify that the producer's `shmName` matches the `inputShmName` property.
- **Resource Intensity**: Dense optical flow is computationally expensive. If the frame rate drops significantly, consider increasing the `computeEveryNFrames` property or reducing the input resolution.
- **Noise Sensitivity**: Classical flow algorithms are sensitive to sensor noise and lighting flickers, which can be interpreted as high-speed motion. Use input stabilization or filtering if the source is noisy.

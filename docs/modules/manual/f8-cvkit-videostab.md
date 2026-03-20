## When to Use

- Use `f8.cvkit.videostab` to remove jitter and small vibrations from a video source (e.g., handheld camera, vibrations on a mounting pole).
- It is most effective as a "preprocessing" step, improving the performance of downstream nodes like trackers, detectors, and optical flow which depend on frame-to-frame stability.
- Use it when you need a "steady-cam" effect for better visual inspection or higher machine vision accuracy.

## Common Wiring Patterns

- **Stable Pipeline Build**: Feed video from `f8.implayer` or `f8.screencap`, process it through the stabilizer, and then provide the *stabilized* output SHM to all other analysis nodes (`detector`, `pose`, `tracking`).
- **A/B Validation**: Temporarily wire one visualizer to the raw source and another to the stabilized output to tune the `smoothing` parameters effectively.
- **Reference Management**: Set a clear `outputShmName` that clearly indicates it's the stabilized version (e.g., `shm.source.stable`) to avoid confusion in complex graphs.

## Pitfalls / Gotchas

- **Edge Artifacts**: Stabilization works by shifting and rotating frames, which can create black bars or "warping" at the edges. You may need to apply a small crop to the output to hide these artifacts.
- **Processing Lag**: Real-time stabilization requires a look-ahead buffer. This adds a small amount of latency to the video path; ensure your control logic is robust to a few frames of delay.
- **Producer Loopback**: Avoid the common mistake of having a consumer read from the raw SHM while expecting stabilized results. Double-check that downstream nodes are explicitly reading from the stabilizer's `outputShmName`.

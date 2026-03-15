## When to Use

- Use `f8.cvkit.flowmetric` to reduce raw per-pixel motion vectors (dense optical flow) into useful scalar fields or summary metrics.
- It is essential when downstream logic needs specific movement qualities like Divergence (expansion/contraction), Curl (rotation), or simple Magnitude rather than raw vectors.
- Best for building "motion-controlled" interfaces where a specific type of movement (e.g., a hand opening or spinning) needs to be mapped to a control value.

## Common Wiring Patterns

- **Motion Control Bridge**: Feed it from `f8.cvkit.denseoptflow`, then normalize and map the resulting scalar output through `f8.pyengine` operators: `Flow Metric` -> `Range Map` -> `Smoothing` -> `Actuator`.
- **Logic Triggers**: Use specific metrics like `divergence` to trigger events when something approaches the camera rapidly.
- **Monitoring**: Visualize the reduced metric over time using `f8.viz.wave` to assist in tuning thresholds for motion-triggered logic.

## Pitfalls / Gotchas

- **Producer Alignment**: The `inputFlowShmName` must match the flow producer's output name exactly. Mismatches are the primary cause of static or empty metric outputs.
- **Sensitivity Tuning**: Metrics can be very sensitive to "micro-motion" or sensor noise. Use the `minMagnitude` or `scale` properties to filter out low-level noise before it reaches your control logic.
- **ROI Dependency**: If the flow is being calculated for the whole frame but your target is only in a small region, the resulting metric may be "diluted." Use input cropping or ROIs if the feature of interest is localized.

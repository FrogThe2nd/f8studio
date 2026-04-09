## When to Use

- Use `UDP Skeleton` to ingest 3D skeleton payloads arriving over a UDP port from external body-tracking software (e.g., custom AI detectors, VR trackers, or proprietary vision systems).
- It is a lightweight, low-latency entry point for getting external body-tracking data into your Feel8 graph.
- Best for scenarios where the vision processing is handled by a separate process or a remote machine.

## Common Wiring Patterns

- **Live Body Ingest**: Feed the `skeleton` output into a `Bone Selector` to pick a joint, or directly into `f8-viz-three-d` to verify coordinate alignment.
- **Monitoring Branch**: Keep a `Print` or `Text Viz` node attached to the incoming stream to monitor the packet rate and verify the protocol schema.
- **Coordinate Calibration**: Use the `offset` and `scale` properties (if available) to align the external skeleton with your graph's internal world-space expectations.

## Pitfalls / Gotchas

- **Network Blocking**: Ensure the target UDP port is open in your OS firewall and not already bound by another process.
- **Convention Clashes**: External sources often use different coordinate systems (e.g., Left-handed vs. Right-handed, Y-up vs. Z-up). If your 3D skeleton looks "inverted" or "laying down," you must apply a transformation early in your graph.
- **Diagnostic Hygiene**: Never attempt to tune your motion logic until you have visually confirmed that the incoming 3D skeleton is stable and correctly oriented in space.

## When to Use

- Use `UDP VMC` to ingest high-quality pose data using the Virtual Motion Capture (VMC) protocol (based on OSC).
- It is the standard entry point for integrating live avatars, VTubing software, and professional mocap-driven skeleton workflows into the Feel8 system.
- Best for scenarios where you want to use refined skeletal data from tools like VSeeFace, Warudo, or other OSC-capable tracking apps.

## Common Wiring Patterns

- **Avatar Logic Chain**: Feed the `selectedSkeleton` output into `Bone Selector` and `Bone Filter`. Use it to drive haptic or visual effects that react to an avatar's posture.
- **Identity Matching**: Use the `availableKeys` and `selectedKey` properties to choose which specific model or person to track if the upstream VMC source is sending data for multiple subjects.
- **Visual Sync**: Position an `f8-viz-three-d` node nearby to verify that the avatar's movements are being captured with the intended fidelity and range of motion.

## Pitfalls / Gotchas

- **Bind Failures**: Ensure the UDP port (default 39539) is correctly configured and that no other VMC app is exclusively locking the port.
- **Latency Spikes**: High-frequency VMC streams over a busy local network can introduce packet loss or jitter. Use `Bone Filter` to stabilize the signal before it reaches your actuators.
- **Scale Issues**: VMC data scales can vary widely between different software implementations. Use a reference visualizer to verify that a "1.0 unit" movement in the VMC source corresponds correctly to your graph's expectations.

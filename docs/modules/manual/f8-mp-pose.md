## When to Use

- Use `f8.mp.pose` when the graph needs human pose landmarks rather than only person boxes.
- It is a natural choice for skeleton-driven visuals, motion analysis, and pose-derived control signals.
- Use it when joints and body structure matter more than generic detection labels.

## Common Wiring Patterns

- Typical inputs come from `f8.implayer`, `f8.screencap`, or live camera-like sources.
- Outputs commonly feed into `f8.viz.track`, `f8.viz.three-d`, or `f8.pyengine`.
- If people are unstable in the frame, consider person localization or cropping before pose estimation.

## Pitfalls / Gotchas

- Pose quality depends heavily on visibility, scale, and occlusion.
- Extreme camera angles can destabilize certain landmarks.
- Before writing control logic, always visualize the landmarks and verify they match the actual body motion.

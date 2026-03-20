## When to Use

- Use `Axis Envelope` when you need to analyze 2D motion (e.g., from a pose tracker or cursor) and extract it as two normalized scalar amplitudes: Major (primary direction) and Minor (secondary direction).
- It is highly effective for converting spatial movement (like hand waving or pelvis motion) into intensity values suitable for driving haptic or visual effects.
- Use it to distinguish between "large swinging" motions and "precise small" movements.

## Common Wiring Patterns

- **Pose Motion Follower**: Feed it `x` and `y` coordinates from `f8-mp-pose`. Map the `major` and `minor` outputs into separate waveforms or device actuator branches.
- **Multidimensional Analysis**: Connect the outputs to `f8-viz-wave` to visually tune the smoothing and normalization parameters while the subject is moving.
- **Gesture Triggering**: Use a threshold on the `major` output to trigger events only when a gesture exceeds a certain spatial intensity.

## Pitfalls / Gotchas

- **Input Scaling**: If the input coordinates are not already normalized (e.g., 0-1) or are poorly scaled, the resulting envelope will be unstable. Verify upstream normalization before tuning the envelope.
- **Coordinate Drift**: If the baseline position of the subject drifts over time, the "center" of the envelope calculation may shift. Use auto-reset or baseline subtraction logic if necessary.
- **Smoothing Response**: Like the standard `Envelope`, excessive smoothing will make the intensity tracking feel sluggish. Balance `attack` and `decay` based on how rapidly the motion is expected to change.

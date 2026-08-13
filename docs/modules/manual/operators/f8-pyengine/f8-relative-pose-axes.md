## When to Use

- Use `Relative Pose Axes` when motion must be measured in a reference bone's
  local coordinate frame instead of world coordinates.
- Use the `L0` output as the raw travel signal for a basic OSR graph.
- Keep all six raw outputs available when a graph may later grow from one-axis
  travel into translation and rotation channels.

## Common Wiring Patterns

- **OSR L0**: Connect reference and target `Bone Selector` outputs, then route
  `L0` through calibration, range mapping, smoothing, and rate limiting before
  `TCode.L0`.
- **Axis Inspection**: Monitor `L0/L1/L2` and `R0/R1/R2` before normalization to
  verify the selected bone orientation.
- **Direction Correction**: Use `invertPrimary` for a reversed reference axis;
  keep device travel limits in a downstream `Range Map`.

## Pitfalls / Gotchas

- **Local Frame Matters**: `primaryAxis=local_y` means the reference bone's Y
  axis, not world Y. A wrong bone rotation can produce plausible but incorrect
  motion.
- **Raw Values Are Not Device Commands**: Translation outputs are geometric
  values and rotation outputs are signed relative values. Normalize and limit
  them before TCode.
- **Missing Pose Is Invalid**: Do not hold the last valid pose as fresh input;
  use `Stream Watchdog` to gate physical output.

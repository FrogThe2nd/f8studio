## When to Use

- Use `3D Viz` when spatial data, 3D poses, skeleton landmarks, or orientation-heavy data needs a geometric preview inside Studio.
- It is the right tool for validating coordinate systems, world-up assumptions, and anatomical motion behavior that is difficult to judge in 2D or text alone.
- Use it to verify depth estimation and spatial relationships between detected objects.

## Common Wiring Patterns

- **Pose Validation**: Feed it pose data (e.g., from `f8-mp-pose`) in parallel with the real downstream logic so spatial correctness can be verified during authoring.
- **Remapping Reference**: Keep a known-good reference skeleton in the view when tuning transforms, remapping axes, or applying bone filters.
- **World Space Check**: Use it to visualize if your "Camera to World" transformations are resulting in realistic object placements.

## Pitfalls / Gotchas

- **Convention Mismatches**: Spatial previews are only as trustworthy as the coordinate conventions feeding them. Wrong handedness (Left vs Right) or axis assumptions (Y-up vs Z-up) can look "plausibly wrong" rather than obviously broken.
- **Sync Issues**: A polished 3D preview does not guarantee that the raw data will align exactly with external consumers (like Unity or Blender) if they expect different scale or pivot conventions.
- **Rendering Cost**: The 3D viewer is more resource intensive than 2D visualizations. Close the 3D tabs when not needed to maximize system performance for inference.

#### When to Use

- Use `3D Viz` when spatial data, poses, tracks, or orientation-heavy outputs need a geometric Studio preview.
- It is the right tool for validating coordinate systems, world-up assumptions, and motion behavior that is hard to judge in text alone.

#### Common Wiring Patterns

- Feed it pose or track-like data in parallel with the real downstream branch so spatial correctness can be verified during authoring.
- Keep a known-good reference source nearby when tuning transforms or remapping axes.

#### Pitfalls / Gotchas

- Spatial previews are only as trustworthy as the coordinate conventions feeding them, so wrong handedness or axis assumptions can look plausibly wrong rather than obviously broken.
- A polished preview does not guarantee the same data will align with downstream consumers that expect different conventions.

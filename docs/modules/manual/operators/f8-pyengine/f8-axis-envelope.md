#### When to Use

- Use `Axis Envelope` when 2D motion should be reduced into normalized major/minor-axis amplitudes.
- It is especially useful for pose- or cursor-like signals that need directional intensity.

#### Common Wiring Patterns

- Feed it `x` and `y`, then map `major` and `minor` into separate wave or device branches.
- Visualize both outputs with `WaveViz` while tuning smoothing and normalization.

#### Pitfalls / Gotchas

- Poorly scaled inputs make the envelope look broken when the problem is really upstream normalization.
- Reset and span settings matter when the motion range shifts over time.


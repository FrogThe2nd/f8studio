#### When to Use

- Use `Wave Pattern` when you want to sketch a looping waveform from explicit control points instead of code.
- It is a strong fit for hand-tuned motion shapes where designers need direct point editing plus predictable interpolation.

#### Common Wiring Patterns

- Feed `t` from a timeline or phase source, edit `points` and `interp` on the node, and send the resulting `value` into envelope, mapping, or output operators.
- Keep the point list sparse at first, then add detail only where the motion shape actually needs it.

#### Pitfalls / Gotchas

- Interpolation choice changes the feel significantly; `pchip`, `akima`, and spline modes can introduce shapes you did not intend if the control points are too dense or uneven.
- `maxT` defines the loop boundary, so point placement near the wrap point needs extra care to avoid discontinuities or misleading previews.

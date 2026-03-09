#### When to Use

- Use `Envelope` when a noisy or fast-changing scalar should become a smoother magnitude trace.
- It is common in audio-reactive and gesture-reactive graphs.

#### Common Wiring Patterns

- Feed it from feature outputs, then send the smoothed result into `Smooth Filter`, `Range Map`, or `WaveViz`.
- Tune it before downstream scaling so later nodes see a stable signal.

#### Pitfalls / Gotchas

- Over-smoothing can make a graph feel laggy even when the source is fine.
- If the input source is mostly wrong or empty, envelope tuning only hides the real issue.


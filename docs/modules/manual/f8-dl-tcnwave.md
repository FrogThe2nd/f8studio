## When to Use

- Use `f8.dl.tcnwave` when you want to map temporal features into a continuous waveform-like output.
- It is a good fit for learned audio-to-control or rhythm-to-wave generation.
- Reach for it when handcrafted rules are becoming too brittle or too fragmented.

## Common Wiring Patterns

- It commonly follows audio features, rhythm features, or another temporal summary path.
- Output is usually forwarded into `f8.pyengine`, TCode-related operators, or visualization.
- Keep a waveform or text inspection branch attached while validating the model behavior.

## Pitfalls / Gotchas

- Output quality depends strongly on how close live inputs are to the model's training distribution.
- Verify time windows, feature ordering, and sampling assumptions before tuning downstream thresholds.
- It is best used for learned style and shaping, not as a blanket replacement for all explicit logic.

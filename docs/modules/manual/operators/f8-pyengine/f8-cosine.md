#### When to Use

- Use `Cosine` when a normalized phase should become a smooth periodic value.
- It is a good building block for simple rhythmic motion or modulation.

#### Common Wiring Patterns

- Feed it from `Phase`, then send `value` into `Range Map`, `WaveViz`, or motion outputs.
- Override `amp` or `dc` from state edges when one waveform needs quick live tuning.

#### Pitfalls / Gotchas

- If the phase source is wrong, tuning amplitude and offset will not fix the waveform.
- Keep output range expectations explicit before wiring it into device-facing nodes.


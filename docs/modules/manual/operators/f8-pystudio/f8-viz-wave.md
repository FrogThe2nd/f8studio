#### When to Use

- Use `Wave Viz` when scalar or waveform outputs need a quick time-based Studio visualization.
- It is especially handy for validating envelopes, expressions, sequences, and mapped control signals before they reach outputs.

#### Common Wiring Patterns

- Attach it to modulation and control branches during tuning so you can compare generated waveforms against expected timing and range.
- Keep it in parallel with the actual output node when debugging device behavior that feels wrong but is hard to quantify.

#### Pitfalls / Gotchas

- The viz helps with shape and timing, but not with downstream semantics like unit meaning or device-specific scaling.
- Very busy graphs can accumulate too many waveform previews, which makes the canvas harder to read than the signal is worth.

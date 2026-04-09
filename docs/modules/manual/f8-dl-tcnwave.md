## When to Use

- Use `f8.dl.tcnwave` when a temporal model should infer a waveform or control trace from a sequence of upstream signals.
- It executes temporal convolution networks (TCN) to map sequences of frames to continuous signals or waveforms, designed for temporal understanding beyond individual frames.
- It is most useful when hand-built mappings are too brittle or too limited.

## Common Wiring Patterns

- Feed it normalized sequential features, then inspect the generated output with `Wave Viz`, `TCodeViz`, or device-output branches.
- Keep the pre-model feature branch visible so release tuning can separate model issues from input issues.

## Pitfalls / Gotchas

- Temporal models depend heavily on input normalization and window assumptions.
- Model output can look unstable if the graph does not match the training-time timing expectations; monitor frame rate consistency.

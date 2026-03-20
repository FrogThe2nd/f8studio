## When to Use

- Use `Wave Viz` when scalar or waveform outputs (numbers over time) need a quick time-based Studio visualization.
- It is especially handy for validating envelopes, expressions, sequences, and mapped control signals before they reach physical outputs or actuators.
- It provides a rolling timeline view that helps you spot jitters, dropouts, or clipping in your signal processing.

## Common Wiring Patterns

- **Modulation View**: Attach it to modulation and control branches during tuning so you can compare generated waveforms against expected timing and range.
- **Actuator Debug**: Keep it in parallel with an actual output node (like `lovense-out` or `serial-out`) when debugging device behavior that feels wrong but is hard to quantify without seeing the data.
- **Smoothing Validation**: Use it to see the "before and after" of a smoothing filter to find the right balance between responsiveness and stability.

## Pitfalls / Gotchas

- **Semantics vs. Shape**: The viz helps with signal shape and timing, but not with downstream semantics like physical unit meaning or device-specific scaling (e.g., 0-1 range vs 0-1024).
- **Canvas Readability**: Very busy graphs can accumulate too many waveform previews, which makes the canvas harder to read. Group related visualizations or delete them once the signal is verified.
- **Resource Usage**: Although lightweight, high-frequency signals with long history buffers in the visualization can consume Studio memory.

#### When to Use

- Use `Smooth Filter` when a scalar already exists but still needs temporal stabilization.
- It is a good second-stage cleanup node after feature extraction or coarse mapping.

#### Common Wiring Patterns

- Place it after `Envelope` or `Range Map`, then compare raw vs filtered signals in parallel on `WaveViz`.
- Keep one filter node per semantic signal so tuning remains readable.

#### Pitfalls / Gotchas

- A filter cannot fix a fundamentally wrong input path.
- Filtering too late can hide where instability actually enters the graph.


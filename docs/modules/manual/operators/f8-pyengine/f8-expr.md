## When to Use

- Use `Python Expr` for simple, one-line stateless transformations where a dedicated operator node would be excessive.
- It is the fastest way to extract a specific field (e.g., `data["centroid"]`), combine a few values (e.g., `(a + b) / 2`), or apply a basic mathematical constant.
- Ideal for data "plumbing" tasks where you needs to quickly reshape a signal.

## Common Wiring Patterns

- **Property Linkage**: Use it to link a service port's output to an operator property that expects a specific numeric range.
- **Signal Aggregation**: Feed a payload into the expression and pass the result into `Range Map`, `Wave Viz`, or state-driven logic ports.
- **Quick Hacking**: Use it to temporarily invert or scale a signal during a tuning session before committing to a more permanent mapping setup.

## Pitfalls / Gotchas

- **Feature Bloat**: If your expression starts needing imports, multi-line logic, or hidden state, it has outgrown the `Python Expr` node. Move that work to a `Data Expr` or `Python Script`.
- **Schema Fragility**: Expressions depend on the exact shape of the input payload. If the upstream node changes its output format, the expression will break silently or raise errors in the logs.
- **Unit Blindness**: It is easy to accidentally combine values with different units or scales (e.g., adding a 0-1 probability and a 0-255 pixel coordinate) if only looking at the code.

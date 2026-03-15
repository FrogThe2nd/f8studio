## When to Use

- Use `Text Viz` when you want a quick, human-readable view of JSON payloads, scalar values, or status text directly on the graph canvas.
- It is often the fastest way to understand what a branch is producing (e.g., the output of a detector or a classifier) without writing custom inspection logic.
- Best for debugging complex data structures where you need to see specific key-value pairs in real-time.

## Common Wiring Patterns

- **Payload Inspection**: Tee a branch into `Text Viz` while developing expressions, state mappings, or service integrations so the raw payload shape stays visible.
- **Node-to-Node Check**: Keep one text inspector near each tricky logic boundary instead of routing everything into a single overloaded debug area.
- **Status Monitoring**: Use it to display high-level status messages or state machine names to know what the graph is doing at a glance.

## Pitfalls / Gotchas

- **Information Overload**: Large or fast-changing payloads (like raw bone coordinates every frame) can become noisy quickly. It is best for spot checks rather than permanent dense telemetry.
- **Invisible Data**: If the payload is hard to interpret here, it usually means the upstream node needs a cleaner schema or better field naming.
- **Performance**: Rendering very large text blocks frequently can cause UI lag in the Studio editor. Filtering the data with `f8-pyexpr` before visualization is recommended.

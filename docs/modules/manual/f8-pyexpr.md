## When to Use

- Use `f8.pyexpr` when you need a simple, single-line Python expression to transform or remap data between other services.
- It is perfect for extracting a specific field from a complex JSON payload, applying a mathematical formula to a signal, or setting up simple "Value Compare" logic without a full `PyEngine` graph.
- Best for lightweight, stateless processing that can be described in a single descriptive string.

## Common Wiring Patterns

- **Signal Extraction**: Feed it structured data (e.g., from `f8.audiofeat.core` or `f8.dl.detector`), write an expression like `data.get("loudness", 0) * 100`, and forward the result to a visualizer or `Text Viz`.
- **Conditional Trigger**: Use it to create a boolean signal (e.g., `data["score"] > 0.8`) that downstream nodes use to activate or deactivate behavior.
- **Payload Rewriting**: Quickly rename or re-format keys in a JSON object before sending it to a generic consumer that expects a specific schema.

## Pitfalls / Gotchas

- **Input Shape Assumptions**: The most common failures are `KeyError` or `AttributeError` caused by unpredictable input data. Use `.get()` or check for key existence if the upstream node sometimes sends empty payloads.
- **Complexity Scope**: If your expression requires multiple lines, imports, or complex state, it has outgrown `f8.pyexpr`. Move that logic into an `f8.pyengine` operator or an `f8.pyscript` service.
- **Runtime Performance**: While fast, evaluate whether high-frequency expressions (e.g., processing every video frame's metadata) can be better handled by a dedicated native operator.

## When to Use

- Use `Data Expr` when you want to execute a compact Python expression over one or more input payloads without the overhead of a full `Python Script` node.
- It is a good fit for minor data transforms, field extraction from JSON, simple conditionals, or unpacking one result into multiple named outputs.
- Best for logic that is too complex for a single property but doesn't require complex state management.

## Common Wiring Patterns

- **Multi-Input Reduction**: Feed it from multiple service or operator data ports. Reference the default input as `x` or use named input ports directly in your expression.
- **Output Unpacking**: Enable `Unpack Dict Outputs` when your expression returns a dictionary. The operator will automatically route values to output ports whose names match the dictionary keys.
- **Signal Gating**: Use a conditional expression (e.g., `x if x > 0.5 else 0`) to gate or filter incoming values before they move further down the graph.

## Pitfalls / Gotchas

- **Complexity Creep**: Expressions stay maintainable only while they are small. Once you need persistent state, complex imports, or multi-step logic, you should upgrade the work to a `Python Script` node.
- **Port Mapping**: Output unpacking only happens for keys that *exactly* match existing output port names. Double-check your spelling!
- **Silent Failures**: Expression errors are often swallowed or only visible in the engine logs. Use `f8-viz-text` to monitor the output if your expression appears to be failing.
- **Library Support**: Optional `numpy` support is available but must be explicitly enabled in node properties.

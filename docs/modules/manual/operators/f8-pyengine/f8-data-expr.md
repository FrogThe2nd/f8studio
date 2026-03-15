#### When to Use

- Use `Data Expr` when you want a compact expression over one or more input payloads without committing to a full script node.
- It is a good fit for small transforms, field extraction, simple conditionals, or unpacking one result into multiple outputs.

#### Common Wiring Patterns

- Feed it from service or operator data ports, reference `x` or named input ports directly, and send the result into mapping, visualization, or control operators.
- Enable `Unpack Dict Outputs` when the expression naturally returns a dict and you want matching output ports to receive each key.

#### Pitfalls / Gotchas

- Expressions stay maintainable only while they are small. Once you need lifecycle behavior, hidden state, or multi-step logic, move the work to `Python Script`.
- Optional `numpy` support is off by default, and output unpacking only happens for keys that exactly match existing output port names.

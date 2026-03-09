#### When to Use

- Use `Python Expr` when a one-line transformation is enough and a full script node would be overkill.
- It is ideal for extracting a field, combining a small number of values, or applying a simple formula.

#### Common Wiring Patterns

- Feed it structured data from services or other operators, then pass the result into `Range Map`, `WaveViz`, or state edges.
- Keep expressions readable enough that the graph still explains itself.

#### Pitfalls / Gotchas

- Once the expression needs lifecycle logic or hidden state, switch to `Python Script`.
- If the payload shape is unclear, the expression node becomes fragile very quickly.


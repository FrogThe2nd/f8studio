## When to Use

- Use `f8.pyexpr` for lightweight one-expression extraction, remapping, and conditional logic.
- It is best for quick field access, formula-based transforms, and simple glue logic.
- It is often faster than spinning up a full `PyEngine` branch when the logic is truly small.

## Common Wiring Patterns

- Place it between two modules as a thin expression layer.
- It is commonly used to turn structured payloads into one clean numeric or boolean output.
- It is a strong choice for quick prototyping and validation.

## Pitfalls / Gotchas

- Once the logic needs multiple steps, imports, or persistent state, move it to `f8.pyengine` or `f8.pyscript`.
- If upstream payload shape is unstable, write defensively rather than assuming one exact structure forever.
- It is best for quick, thin logic, not for the most important long-term business rules in the graph.

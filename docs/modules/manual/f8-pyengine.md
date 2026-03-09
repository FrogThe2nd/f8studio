## When to Use

- Use `f8.pyengine` as the main runtime host for `f8.*` operators, signal transforms, protocol adapters, and custom control logic.
- It is the default choice when a graph needs composable operator chains rather than a standalone service.

## Common Wiring Patterns

- Create one or more `PyEngine` host nodes, then bind each operator `Service Id` to the correct host node `id`.
- Separate timing/wave generation, mapping, and device-output branches so release debugging stays tractable.

## Pitfalls / Gotchas

- Missing or wrong `Service Id` is the most common reason an operator graph looks correct but does nothing.
- Overloading one host with too many unrelated responsibilities makes runtime diagnosis and reuse harder.


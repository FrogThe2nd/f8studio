## When to Use

- Use `f8.pyexpr` for lightweight service-level expression evaluation when a full `PyEngine` operator graph is unnecessary.
- It is good for extracting one value, remapping a payload, or applying a simple computed rule.

## Common Wiring Patterns

- Feed it structured data from feature or CV services, then forward the reduced output into `f8.pyengine`, `TextViz`, or state edges.
- Keep expressions small and explicit; move complex flow into `f8.pyengine` or `f8.pyscript`.

## Pitfalls / Gotchas

- Expression failures often come from unclear input payload shape rather than Python syntax alone.
- If the expression starts carrying workflow state, it has outgrown this service.


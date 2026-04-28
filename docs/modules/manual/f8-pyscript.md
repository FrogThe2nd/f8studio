## When to Use

- Use `f8.pyscript` for Python logic that has outgrown a single expression but is not yet formalized as dedicated operators.
- It is useful for multi-line scripts, custom experimentation, and intermediate prototype logic.
- It works well as a temporary home for evolving graph behavior.

## Common Wiring Patterns

- Use it as a bounded custom logic layer while the design is still changing.
- Once the behavior stabilizes, consider moving the logic into `PyEngine` operators or a dedicated service.
- Keep inputs and outputs explicit so the script block does not become an unreadable center of gravity.

## Pitfalls / Gotchas

- If the script keeps getting longer and harder to test, it is time to refactor.
- Clear input/output schemas help far more than "we will clean it up later".
- If the behavior should be reused across graphs, promote it into a more explicit module or operator.

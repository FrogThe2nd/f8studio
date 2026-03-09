#### When to Use

- Use `State Trigger` when exec should fire only when a watched state value changes.
- It is useful for event-like reactions without polling every tick.

#### Common Wiring Patterns

- Feed a stateful value into it, then use the `changed` exec output to gate side effects or downstream updates.
- Pair it with `ControlPanel` or service state edges when authoring reactive graphs.

#### Pitfalls / Gotchas

- If the watched value changes every frame, this becomes effectively another tick source.
- Make sure the graph really wants change detection and not periodic evaluation.


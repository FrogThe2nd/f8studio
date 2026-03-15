#### When to Use

- Use `Control Panel` when you want a lightweight Studio-local UI surface for tweaking graph values during authoring.
- It is a good fit for live tuning sessions where direct interaction is more helpful than editing raw properties repeatedly.

#### Common Wiring Patterns

- Pair it with runtime nodes that expose meaningful state fields, then use the panel as a focused place to adjust only the values you care about during a session.
- Keep the panel near the branch it controls so the graph still reads clearly when revisited later.

#### Pitfalls / Gotchas

- It is an editor aid, not a substitute for clear runtime defaults and explicit graph structure.
- Overusing custom controls can hide which underlying node state actually matters.

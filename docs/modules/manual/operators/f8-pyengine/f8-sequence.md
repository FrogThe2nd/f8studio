#### When to Use

- Use `Sequence` when one exec trigger should fan out into ordered branches.
- It is the cleanest way to make evaluation order explicit on canvas.

#### Common Wiring Patterns

- Place it immediately after `Tick` to separate read, transform, and output branches.
- Use different numbered outputs for side effects that should not race each other.

#### Pitfalls / Gotchas

- `Sequence` controls ordering, not timing isolation; expensive branches still affect the whole tick.
- Overusing many nested `Sequence` nodes can make graphs harder to read than separate well-named branches.


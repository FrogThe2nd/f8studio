#### When to Use

- Use `Bone Selector` when a full skeleton should be reduced to one named bone payload.
- It is the cleanest bridge between skeleton ingest and bone-specific control logic.

#### Common Wiring Patterns

- Feed it from `UDP Skeleton` or `UDP VMC`, then pass the selected bone into `Bone Filter` or `Quat To Euler`.
- Watch `availableBones` while authoring so the chosen target name is valid.

#### Pitfalls / Gotchas

- A missing bone name is often a naming mismatch, not a broken upstream skeleton.
- Keep source-skeleton validation in place; this node cannot fix a malformed pose stream.


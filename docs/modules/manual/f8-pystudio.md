## When to Use

- Use `f8.pystudio` for editor-local operators that exist to inspect, annotate, or interact with a graph inside Studio itself.
- It is the built-in host for visualization and utility nodes that should stay local to the UI instead of deploying as standalone services.

## Common Wiring Patterns

- Keep `f8.pystudio` operators close to the branches they help explain, such as previews, notes, or local controls attached to active runtime graphs.
- Use Studio-local operators for inspection and authoring feedback while leaving core runtime behavior in services like `f8.pyengine`, `f8.dl.*`, or capture nodes.

## Pitfalls / Gotchas

- `f8.pystudio` operators are editor-local, so they should not be treated as remote runtime building blocks in the same way as deployable services.
- Graphs become harder to port or automate if critical behavior depends on visualization nodes instead of explicit runtime operators.

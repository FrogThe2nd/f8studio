#### When to Use

- Use `Lovense Mock Server` when you need to test Lovense Local API integrations without the real device stack.
- It is valuable for release rehearsals, demos, and protocol debugging.

#### Common Wiring Patterns

- Keep it in a side branch used for validation and automated checks, not as the default production path.
- Inspect emitted event state and exec triggers with `Print` or `TextViz` while testing adapters.

#### Pitfalls / Gotchas

- It validates protocol flow, not real hardware behavior.
- Binding issues and loopback settings are the first things to check when it appears silent.


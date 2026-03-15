#### When to Use

- Use `Text Viz` when you want a quick readable view of payloads, scalar values, or status text on the canvas.
- It is often the fastest way to understand what a branch is producing without writing custom inspection logic.

#### Common Wiring Patterns

- Tee a branch into `Text Viz` while developing expressions, state mappings, or service integrations so payload shape stays visible.
- Keep one text inspector near each tricky boundary instead of routing everything into a single overloaded debug area.

#### Pitfalls / Gotchas

- Large or fast-changing payloads can become noisy quickly, so a text view is best for spot checks rather than permanent dense telemetry.
- Readability can hide missing schema discipline; if the payload is hard to interpret here, it may need cleaner upstream structure.

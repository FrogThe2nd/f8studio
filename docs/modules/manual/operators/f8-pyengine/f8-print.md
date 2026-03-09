#### When to Use

- Use `Print` when you need a quick exec-driven debug sink on canvas.
- It is best for development and release validation, not polished production UX.

#### Common Wiring Patterns

- Trigger it from `Tick` or `Sequence` and pull the value you want to inspect on that branch.
- Keep it close to the stage you are diagnosing so the log reflects the right execution context.

#### Pitfalls / Gotchas

- Leaving many `Print` nodes active in a hot loop can drown out more important logs.
- It is a sink; if you need persistent visualization, use `TextViz` or `WaveViz` instead.


#### When to Use

- Use `Pull` only when a graph needs an explicit auto-sampling sink for upstream data nodes.
- Treat it as an advanced/internal helper rather than a default authoring node.

#### Common Wiring Patterns

- Use it to force periodic upstream evaluation in graphs that are otherwise data-only.
- Keep it isolated and clearly labeled when it exists in a shared session.

#### Pitfalls / Gotchas

- It can hide the real execution model if used casually.
- Prefer clear exec flow first; reach for `Pull` only when the graph genuinely needs passive sampling.


## When to Use

- Use `f8.proclauncher` when the graph needs to start an external helper tool, bridge, browser, or companion process.
- It is useful when a scenario depends on non-Feel8 software being started alongside the graph.
- It works best as a lifecycle helper rather than as a data-processing module.

## Common Wiring Patterns

- Treat it as a runtime dependency node rather than a transformation node.
- Keep it near the part of the graph that depends on the launched tool so the dependency is visually obvious.
- If only one instance should exist, keep singleton-style settings enabled.

## Pitfalls / Gotchas

- Incorrect path quoting is the most common failure mode, especially on Windows.
- Detached processes can survive after the graph stops if that behavior is enabled.
- Prefer absolute paths when the launched tool depends on working directory or environment assumptions.

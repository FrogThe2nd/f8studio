## When to Use

- Use `f8.screencap` when the graph should read the desktop, a monitor, or a region as a live video SHM source.
- It is the usual producer for screen-driven CV and UI automation scenarios.

## Common Wiring Patterns

- Feed its video SHM into CVKit, DL, pose, or `f8.viz.video` consumers.
- Keep a parallel visualization branch while locking capture region, scale, and frame timing.

## Pitfalls / Gotchas

- Wrong monitor, region, or permission setup can look like a dead downstream graph.
- Release builds should validate the same capture mode used in the final scenario, not just any available screen source.


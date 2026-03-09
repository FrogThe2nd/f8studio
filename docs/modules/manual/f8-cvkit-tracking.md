## When to Use

- Use `f8.cvkit.tracking` when you need a continuously updated ROI after initialization.
- It fits graphs where the target keeps moving but still stays visually trackable frame to frame.

## Common Wiring Patterns

- Start with `f8.cvkit.templatematch` or a capture workflow for initialization, then keep `f8.viz.track` attached for monitoring.
- Feed tracked regions into `f8.pyengine` or downstream CV services when graph logic depends on target position.

## Pitfalls / Gotchas

- Tracker drift is easy to miss if you only inspect terminal outputs and not the overlay node.
- Startup order matters when the tracker expects a ready input stream and valid initial region.


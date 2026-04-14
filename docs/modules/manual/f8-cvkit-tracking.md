## When to Use

- Use `f8.cvkit.tracking` after a target has already been initialized and you want to keep following it over time.
- It is a good fit for moving people, objects, or screen regions that remain visually coherent across frames.
- It is usually more suitable than repeated matching once the target has been acquired.

## Common Wiring Patterns

- A common flow is `templatematch / manual ROI -> tracking`.
- During development, keep `f8.viz.track` attached so loss-of-lock is immediately visible.
- The tracking output is often forwarded into `f8.pyengine` for position-based rules, region triggers, or follow logic.

## Pitfalls / Gotchas

- Occlusion, rapid acceleration, and large scale change can all cause loss of lock.
- Track drift can build up gradually; periodic re-initialization from a detector or matcher is often needed.
- If the initial box is unreliable, long-term tracking usually will be too.

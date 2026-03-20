## When to Use

- Use `Track Viz` when you want to inspect temporal or positional tracking outputs (trajectories, IDs, bounding boxes) directly inside Studio.
- It is useful for checking tracking continuity, ID persistence, and movement jitter while tuning tracking-oriented graphs.
- Use it to visualize the "history" of a tracked object to understand its path over time.

## Common Wiring Patterns

- **Persistence Check**: Branch tracker-like outputs (e.g., from `f8-cvkit-tracking`) into the viz while preserving the main runtime path for whatever consumes the same data downstream.
- **Combined View**: Use it side-by-side with raw video or text inspection when you need both spatial intuition and exact payload ID confirmation.
- **Tuning Filter**: Visualize the raw vs. filtered track to find the right balance for your tracking algorithm's search window.

## Pitfalls / Gotchas

- **Identity Confusion**: A stable-looking track view can still hide semantic mistakes like wrong target association or stale IDs if the view doesn't distinguish between different track instances clearly.
- **Visual Clutter**: Clutter grows fast when too many objects are being tracked at once. Narrow the scope of the inspected branch when debugging specific target behaviors.
- **Lag**: Displaying a long history of many paths simultaneously can induce UI lag; limit the history length if the Studio canvas feels sluggish.

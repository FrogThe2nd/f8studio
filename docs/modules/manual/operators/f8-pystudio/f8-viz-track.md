#### When to Use

- Use `Track Viz` when you want to inspect temporal or positional tracking outputs directly inside Studio.
- It is useful for checking continuity, jitter, and identity behavior while tuning tracking-oriented graphs.

#### Common Wiring Patterns

- Branch tracker-like outputs into the viz while preserving the main runtime path for whatever consumes the same data downstream.
- Use it side by side with raw video or text inspection when you need both spatial intuition and exact payload confirmation.

#### Pitfalls / Gotchas

- A stable-looking track view can still hide semantic mistakes like wrong target association or stale identities.
- Visual clutter grows fast when too many tracks are shown at once, so it helps to narrow the inspected branch when debugging.

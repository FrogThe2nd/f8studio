## When to Use

- Use `f8.cvkit.videostab` when camera or capture jitter hurts downstream CV quality.
- It is most useful when stabilization is part of preprocessing, not as a cosmetic final pass.

## Common Wiring Patterns

- Feed it from `f8.implayer` or `f8.screencap`, then hand the stabilized output to detection, tracking, or pose services.
- Validate the stabilizer before tuning downstream models, otherwise quality issues get misattributed.

## Pitfalls / Gotchas

- Stabilization can add crop, lag, or edge artifacts that later services still need to tolerate.
- A wrong output SHM assumption can make downstream consumers silently keep reading the unstabilized source.


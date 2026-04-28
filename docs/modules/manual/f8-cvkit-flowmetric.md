## When to Use

- Use `f8.cvkit.flowmetric` when you want compact motion metrics instead of a full optical flow field.
- It is a good fit for activity level, direction bias, and threshold-driven motion logic.
- In practice it is often easier to build control logic from these summary metrics than from raw dense flow.

## Common Wiring Patterns

- A common chain is `video source -> f8.cvkit.denseoptflow -> f8.cvkit.flowmetric -> f8.pyengine`.
- It is especially useful when the next step is state switching, thresholding, or numerical remapping.
- Keep a preview branch available during early tuning so motion spikes can be compared against the actual image.

## Pitfalls / Gotchas

- If the metrics feel abstract, go back to the original video or dense flow view rather than tuning blindly.
- Scene cuts, flashes, and abrupt camera changes can create extreme motion peaks.
- If you really need object-level tracking rather than global motion, use `templatematch` or `tracking` instead.

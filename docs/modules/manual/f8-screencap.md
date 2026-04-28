## When to Use

- Use `f8.screencap` when the graph needs live desktop, monitor, or region capture as a video source.
- It is a standard choice for UI vision, screen analysis, game capture, and desktop automation scenarios.
- Reach for it when the graph needs to interpret what is currently on the screen.

## Common Wiring Patterns

- A common chain is `f8.screencap -> CV / DL / Viz`.
- While tuning capture area and scale, keep a `f8.viz.video` branch connected for visual confirmation.
- If later modules only care about a small region, crop early rather than feeding a full screen at full rate.

## Pitfalls / Gotchas

- Screen recording permissions, OS security policies, and GPU compatibility are common blockers.
- Full-screen high-resolution capture is expensive; reduce area or scale whenever possible.
- If only one button, panel, or HUD matters, capture only that region instead of the entire desktop.

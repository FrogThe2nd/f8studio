## When to Use

- Use `f8.dl.humandetector` when the graph only cares about people rather than general objects.
- It is a strong choice for human-presence logic, person ROI filtering, and person-first analysis chains.
- It usually fits better than a general detector when the scene is fundamentally human-centered.

## Common Wiring Patterns

- A common chain is `video source -> f8.dl.humandetector -> tracking / mp.pose / pyengine`.
- If later logic depends on people, stabilize the "where is the person?" step before building the rest of the graph.
- Keep detections visible during development.

## Pitfalls / Gotchas

- Small people, strong backlight, and heavy occlusion reduce quality quickly.
- Human detection is not the same as pose estimation; use it as a person-localization stage, not as a skeleton source.
- In multi-person scenes, decide early which person the graph should follow.

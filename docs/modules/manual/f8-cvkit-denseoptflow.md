## When to Use

- Use `f8.cvkit.denseoptflow` when you care about motion across the whole frame rather than just one tracked object.
- It is useful for global movement analysis, camera motion estimation, and motion-driven control signals.
- Reach for it when you need "where the frame is moving" rather than "where one target is".

## Common Wiring Patterns

- Feed it from `f8.implayer` or `f8.screencap`.
- Keep a video preview branch nearby during setup so you can compare motion output with the original image.
- If you only need compact motion summaries, hand the result off to `f8.cvkit.flowmetric` or `f8.pyengine`.

## Pitfalls / Gotchas

- High resolution and high frame rate can make dense flow expensive quickly; verify you really need full-quality input.
- Compression artifacts, flicker, and capture jitter can distort the result.
- If downstream logic is too sensitive, add smoothing or thresholding before using the flow values as control signals.

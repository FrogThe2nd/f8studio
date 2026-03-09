#### When to Use

- Use `Bone Filter` when one bone pose should be smoothed and converted into a more stable local signal.
- It is ideal for skeleton-driven control graphs where jitter matters.

#### Common Wiring Patterns

- Feed it from `Bone Selector`, then use `filtered` or `relative` outputs for mapping, Euler conversion, or visualization.
- Tune it with a live skeleton viewer attached so resets and lag are easy to spot.

#### Pitfalls / Gotchas

- Do not tune the filter before confirming the selected bone stream is correct.
- Jump-reset settings can mask coordinate or source glitches if they are too aggressive.


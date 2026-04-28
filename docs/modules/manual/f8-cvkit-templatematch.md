## When to Use

- Use `f8.cvkit.templatematch` to find the best match for a known reference patch inside the frame.
- It is well suited to buttons, icons, UI elements, and other visually stable anchors.
- It is also a common way to initialize a region before handing off to tracking.

## Common Wiring Patterns

- Feed it from `f8.screencap` or `f8.implayer`.
- Keep a visual overlay or preview attached while tuning so you can confirm the match box is landing where expected.
- For long-lived following behavior, use "match first, then track".

## Pitfalls / Gotchas

- Matching degrades quickly when the live target differs too much in scale, rotation, or lighting.
- Template matching is not a general detector; it works best when appearance is mostly stable.
- Threshold tuning should be evaluated against both false positives and misses, not just a single good frame.

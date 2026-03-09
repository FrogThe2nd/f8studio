## When to Use

- Use `f8.audiofeat.core` for low-level descriptors such as loudness, spectrum-derived features, and general audio activity.
- It is the default feature block for audio-reactive graphs that later map values into motion or visualization.

## Common Wiring Patterns

- Feed it from `f8.audiocap`, then branch outputs to `TextViz`, `Python Expr Service`, or `f8.pyengine` operators.
- Keep `windowMs` and `hopMs` aligned with the responsiveness you need before adding extra smoothing in `f8.pyengine`.

## Pitfalls / Gotchas

- Forgetting to wire the correct `audioShmName` makes the service appear idle rather than explicitly broken.
- Oversized windows improve stability but can make the final motion path feel delayed.


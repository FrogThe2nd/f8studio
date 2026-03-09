## When to Use

- Use `f8.audiofeat.rhythm` when beat, onset, or tempo-like cues matter more than raw energy.
- It complements `f8.audiofeat.core` rather than replacing it.

## Common Wiring Patterns

- Feed the same Audio SHM from `f8.audiocap` into both `core` and `rhythm` services.
- Visualize rhythm outputs with `TextViz` or map them into `Tick`, `Envelope`, or `Range Map` driven logic.

## Pitfalls / Gotchas

- Rhythm features can look sparse if the source material lacks sharp transients.
- Users often overfit downstream thresholds before checking whether the input audio itself is strong enough.


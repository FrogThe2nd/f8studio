## When to Use

- Use `f8.audiofeat.core` to extract continuous audio features such as loudness, peak level, spectral centroid, and spectral change.
- It is a good fit for audio-driven visuals, device control, silence detection, and general energy tracking.
- In many graphs it is the foundation of the audio analysis path.

## Common Wiring Patterns

- A common chain is `f8.audiocap -> f8.audiofeat.core -> f8.pyengine`.
- If you just want to inspect the analysis result, route the output to `f8.viz.text` or another lightweight visualizer.
- For smooth control behavior, follow it with operators such as `Range Map`, filters, or envelope shaping.

## Pitfalls / Gotchas

- Raw energy ranges are often source-dependent, so normalization or remapping is usually needed before driving sensitive controls.
- Larger `windowMs` tends to improve stability; smaller `hopMs` improves responsiveness but raises CPU cost.
- If the module is producing numbers but the graph behavior feels erratic, inspect input level and feature range before rewriting downstream logic.

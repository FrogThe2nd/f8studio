## When to Use

- Use `f8.audiofeat.rhythm` when timing cues such as onset, beat, and tempo matter more than continuous energy.
- It is well suited to beat-synced visuals, trigger logic, and percussion-like event detection.
- Pair it with `f8.audiofeat.core` when you want both intensity and timing from the same source.

## Common Wiring Patterns

- A common setup sends one `f8.audiocap` stream into both `core` and `rhythm`.
- Rhythm events are often routed into `f8.pyengine` trigger logic, state machines, or envelope operators.
- While tuning thresholds, keep a text or log branch attached so you can validate event density in real time.

## Pitfalls / Gotchas

- Rhythm analysis depends heavily on transients; ambient or texture-heavy material may look weak or inconsistent.
- Low thresholds can flood the graph with false triggers, while high thresholds can make the service appear silent.
- Tempo estimation often needs a short settling period, so do not treat it as instantly stable on very short clips.

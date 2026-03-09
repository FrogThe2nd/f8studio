## When to Use

- Use `f8.audiocap` when a graph needs live microphone or loopback audio as its timing and feature source.
- Keep it near the front of audio-driven graphs so downstream services can share one stable Audio SHM producer.

## Common Wiring Patterns

- Pair it with `f8.audiofeat.core`, `f8.audiofeat.rhythm`, and `f8.viz.audio`.
- Reuse the same audio SHM name across capture, feature extraction, and visualization branches.

## Pitfalls / Gotchas

- Wrong input device or host API selection looks like a dead graph even when deploy succeeds.
- Mismatched sample-rate expectations downstream can make feature services look unstable or empty.


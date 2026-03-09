#### When to Use

- Use `Playback Sync` when a graph needs a timeline or state derived from media/sequence playback progress.
- It helps tie motion logic to a shared playback clock.

#### Common Wiring Patterns

- Pair it with `Sequence Player`, `IM Player`, or other time-based sources so downstream operators can lock to the same progress.
- Keep sync state visible during authoring to confirm the graph follows the intended master clock.

#### Pitfalls / Gotchas

- Competing timebases are a common source of drift and surprise resets.
- If playback ownership is unclear, downstream timing bugs are hard to localize.


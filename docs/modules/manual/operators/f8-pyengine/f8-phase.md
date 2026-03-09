#### When to Use

- Use `Phase` when a graph needs a normalized oscillator or continuous cycle counter.
- It is the default upstream driver for `Cosine`, `Tempest`, and many rhythmic graphs.

#### Common Wiring Patterns

- Feed `phase` into waveform generators and send `phaseTurns` into debug or sync branches.
- Use state or data overrides for `hz`, `phase`, and `reset` when the clock must react live.

#### Pitfalls / Gotchas

- If the graph already has an authoritative timebase, adding another `Phase` can make behavior harder to reason about.
- Reset behavior should be verified with a visual branch before it drives hardware.


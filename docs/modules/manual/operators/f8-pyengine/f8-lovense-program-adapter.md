#### When to Use

- Use `Lovense Program Adapter` when generic motion/control signals should become Lovense-style program semantics.
- It isolates protocol translation from the rest of the graph.

#### Common Wiring Patterns

- Place it after waveform generation and normalization, then feed the adapted output into `Lovense Out`.
- Keep the generic motion branch visible in parallel so adapter behavior is easy to compare.

#### Pitfalls / Gotchas

- If the source signal is poorly normalized, adapter tuning becomes guesswork.
- Mixing protocol mapping with source-signal cleanup makes both stages harder to reuse.


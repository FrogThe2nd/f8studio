#### When to Use

- Use `Audio Viz` when you need a Studio-local view of audio-related signals while tuning a graph.
- It is helpful for verifying timing, amplitude, and overall shape before those values drive downstream behavior.

#### Common Wiring Patterns

- Attach it to audio or scalar outputs in parallel with the real processing branch so you can inspect the signal without changing the runtime path.
- Use it during threshold or mapping work, then keep it around as a debug branch if the scenario is sensitive.

#### Pitfalls / Gotchas

- Visualization confirms what Studio sees locally, not necessarily every deployment condition.
- If the inspected signal is already transformed or clipped upstream, the viz can make a bad source look deceptively reasonable.

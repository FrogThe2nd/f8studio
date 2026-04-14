## When to Use

- Use `f8.audiocap` as the audio capture entry point for microphone, loopback, or ASIO input.
- Choose it when the graph needs a reusable low-latency audio SHM producer.
- It is the usual starting point for audio-reactive graphs.

## Common Wiring Patterns

- A standard chain is `f8.audiocap -> f8.audiofeat.core / f8.audiofeat.rhythm`.
- During setup, keep a `f8.viz.audio` branch connected so you can confirm that audio is really flowing.
- If multiple services consume the same stream, keep `audioShmName` consistent across the branch.

## Pitfalls / Gotchas

- If nothing moves, first verify the selected capture device and whether another app has locked it.
- Sample rate and buffer size directly affect latency and stability; smaller buffers are more responsive but easier to destabilize.
- When downstream audio logic looks wrong, confirm the capture source is healthy before tuning the analysis layers.

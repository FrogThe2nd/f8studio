## When to Use

- Use `f8.audiocap` when a graph needs live microphone input, system audio loopback, or ASIO-based audio as its primary timing and feature source.
- It acts as the canonical Audio Shared Memory (SHM) producer, providing raw signal data to any number of analysis nodes without redundant captures.
- Keep it at the "head" of audio-driven graphs so downstream services can share one stable, low-latency audio stream.

## Common Wiring Patterns

- **Standard Analysis**: Pair it with `f8.audiofeat.core` for energy/loudness and `f8.audiofeat.rhythm` for beat detection.
- **Monitoring**: Connect the audio SHM to `f8.viz.audio` for real-time waveform and spectrogram visualization.
- **Multi-Branching**: Reuse the same configured `audioShmName` (e.g., `shm.audiocap.mic`) across capture, feature extraction, and playback visualization branches.

## Pitfalls / Gotchas

- **Device Selection**: Choosing the wrong input device or host API (MME vs ASIO vs WASAPI) is the most common reason for a "dead" graph. Verify the device is available and not exclusively locked by another application.
- **Sample Rate Mismatches**: Ensure the capture sample rate matches what downstream feature services expect. Large mismatches can make pitch or rhythm services look unstable or produce empty data.
- **Buffer Latency**: If the graph feels sluggish, check the buffer size settings. Small buffers reduce latency but increase CPU overhead and risk of "crackle."

## When to Use

- Use `Audio Viz` when you need a Studio-local view of audio-related signals (waveforms, spectrograms, or extracted spectral features) while tuning a graph.
- It is essential for verifying timing, amplitude ranges, and the presence of specific frequency content before those values drive downstream behavior.
- Use it to compare different audio feature extractors (e.g., Core vs Rhythm) on the same source.

## Common Wiring Patterns

- **Parallel Monitoring**: Attach it to Zenoh audio streams or feature outputs in parallel with the real processing branch so you can inspect the signal without changing the runtime signal path.
- **Threshold Work**: Use it when setting noise floors or onset thresholds to see the signal peaks and valleys clearly.
- **Spectrum Analysis**: Use the spectrogram view to identify specific noise sources or frequency-based patterns that you want to trigger on using `f8-audiofeat-core`.

## Pitfalls / Gotchas

- **Studio Local Perspective**: The visualization confirms what the Studio environment sees locally, which might differ from a remote deployment if network or audio device configurations vary.
- **Upstream Clipping**: If the inspected signal is already transformed, normalized, or clipped upstream, the visualization can make a "broken" or low-quality source look deceptively reasonable.
- **Resource Consumption**: Real-time FFT and spectrogram rendering are compute-heavy; avoid keeping many High-Res audio visualizers open if you are running on resource-constrained hardware.

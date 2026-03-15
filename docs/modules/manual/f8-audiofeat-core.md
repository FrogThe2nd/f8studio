## When to Use

- Use `f8.audiofeat.core` to extract low-level acoustic descriptors such as loudness (RMS/Peak), spectrum-derived features (Centroid, Flux), and general audio activity level.
- it is the foundation for audio-reactive graphs that map real-world sound energy into visual motion, device control, or logic triggers.
- Choose this for general energy tracking, silence detection, or basic timbre analysis.

## Common Wiring Patterns

- **Energy to Motion**: Feed it from `f8.audiocap`, then branch the `loudness` or `centroid` outputs to `f8.pyengine` operators or `Python Expr` for range mapping and smoothing.
- **Spectrum Viz**: Connect the `spectrum` output to visualization nodes to inspect the frequency distribution in real-time.
- **Responsive Tuning**: Adjust `windowMs` and `hopMs` to balance responsiveness vs. stability. Longer windows provide smoother features at the cost of slight latency.

## Pitfalls / Gotchas

- **SHM Connectivity**: Forgetting to wire the correct `audioShmName` is a common mistake. If the service starts but shows no activity, verify it's reading from the correct producer's SHM region.
- **Normalization**: Raw audio energy can vary wildly between sources. Use a `Range Map` or auto-gain logic in `f8.pyengine` to normalize features before they drive sensitive actuators.
- **Processing Overhead**: High-resolution spectral analysis (very short `hopMs`) can be CPU intensive. Only use high rates if the downstream control logic actually requires sub-10ms updates.

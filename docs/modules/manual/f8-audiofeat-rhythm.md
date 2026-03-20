## When to Use

- Use `f8.audiofeat.rhythm` when timing-based cues such as onsets (drastic changes), beats, or estimated tempo matter more than raw signal energy.
- It is designed to complement `f8.audiofeat.core`, providing "event" timing rather than continuous signal levels.
- Best for music-synced visualization, beat-driven interactions, or detecting sudden percussive sounds (claps, impacts).

## Common Wiring Patterns

- **Multi-Analysis Branch**: Feed the same Audio SHM from `f8.audiocap` into both `core` and `rhythm` services to get a complete picture of the sound.
- **Beat-Triggered Logic**: Use the `onset` or `beat` outputs as triggers for `Tick`, `Envelope`, or state machines in `f8.pyengine`.
- **Visualization**: Output rhythm cues to `TextViz` or custom visual overlays to verify detection accuracy against the live audio.

## Pitfalls / Gotchas

- **Material Dependency**: Rhythm features rely on transients. Detection will look sparse or inconsistent if the source material is very ambient, drone-like, or lacks sharp attacks.
- **Threshold Sensitivity**: Onset detection is highly sensitive to background noise. Tune the `threshold` properties carefully while watching the visual feedback to avoid false positives in noisy environments.
- **Latency Consistency**: Beat tracking algorithms often need a few seconds of consistent audio to "lock on" to a tempo. Avoid relying on instant tempo accuracy for short-duration sounds.

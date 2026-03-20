## When to Use

- Use `Mix (Silence Fill)` when your graph combines multiple signal sources (e.g., from different detectors or audio streams) and you want to ensure the output remains continuous even if one or more branches stop sending data.
- It is the standard way to "blend" multiple control inputs into a single authoritative signal (e.g., mixing a manual slider with an automated vision trigger).
- Best for building robust scenarios that shouldn't "freeze" or "glitch" when a sensor port is temporarily disconnected.

## Common Wiring Patterns

- **Safe Blending**: Combine multiple upstream branches before final scaling or mapping nodes. This allows you to inspect one combined, high-level signal instead of managing dozens of individual branches.
- **Fall-Through Monitoring**: Keep a `WaveViz` immediately after the mixer to observe how different signals are being prioritized or added together in real-time.
- **Default State Engine**: Use the mixer to provide a "Default" or "Base" set of values that are only overridden when higher-priority signals become active.

## Pitfalls / Gotchas

- **Hidden Failures**: Because this node "fills" missing data with silence or neutral values, it can hide genuine upstream outages (like a crashed camera service). Use separate `monitor` ports or health visualizers to track individual branch health.
- **Normalization Drifts**: If you mix multiple signals without prior range mapping, a single high-intensity branch can "drown out" all others. ensure your inputs are normalized to a consistent 0-1 range before mixing.
- **Over-Abstraction**: Mixing too early in your logic chain can make it very difficult to debug branch-specific issues, as you are only ever looking at the final "stew" of data.

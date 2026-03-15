## When to Use

- Use `Lovense Program Adapter` to translate generic motion signals or "Program" payloads into the specific command semantics required by the Lovense hardware protocol.
- It is the specialized translation layer that isolates device-specific logic (like intensity rounding and specific vibration modes) from your abstract graph motion.
- Ideal for complex scenarios where you want one motion source to control a variety of Lovense-compatible toys with different capabilities.

## Common Wiring Patterns

- **Device Adaptation**: Place it immediately after your main waveform generation (`f8-cosine`, `f8-wave-pattern`) and normalization (`Range Map`). Feed the adapted result directly into a `Lovense Out` node.
- **Side-by-Side Comparison**: Keep the generic "source" motion branch visible in parallel with the "adapted" output using `f8-viz-wave` to verify that the translation is faithful to the original intent.
- **Profile Switching**: Use the adapter to apply different "profiles" to the same signal (e.g., mapping a stroke to a vibration intensity vs. a rotation speed).

## Pitfalls / Gotchas

- **Input Normalization**: If the source signal is poorly scaled or unnormalized (e.g., values > 1.0), the adapter's internal logic will produce unpredictable results or clipped intensity commands. High-quality input is a prerequisite.
- **Mixed Responsibilities**: Do not attempt to fix signal jitter or add delays inside the adapter. Handle signal cleanup in `Smooth Filter` and logic in `f8-pyengine`. The adapter should be a clean 1:1 translation stage.
- **Rounding Artifacts**: Lovense devices have discrete intensity steps. If your source signal is very subtle, the adapter's rounding may lead to a "steppy" feeling in the physical vibration.

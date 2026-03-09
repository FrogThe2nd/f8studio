#### When to Use

- Use `Buttplug Out` when the final control path targets a Buttplug-compatible device.
- It is the adapter node for Buttplug-specific runtime output.

#### Common Wiring Patterns

- Feed it cleaned, bounded control values rather than raw feature outputs.
- Keep the protocol branch separate from generic signal shaping so the graph stays portable.

#### Pitfalls / Gotchas

- Device capability mismatches are common; confirm the target supports the intended command style.
- A bad upstream range will feel like a protocol problem even when transport is fine.


## When to Use

- Use `VMC Decoder` after `UDP In` when the incoming UDP packets carry VMC OSC messages.
- It is the canonical replacement for the removed `UDP VMC` operator.
- This split keeps OSC/VMC decoding independent from socket lifecycle, which makes graphs more composable and easier to debug.

## Common Wiring Patterns

- **Avatar Pipeline**: Connect `UDP In.packet` to `VMC Decoder.packet`, then use `selectedSkeleton` for `Bone Selector`, `Bone Filter`, or avatar-driven control logic.
- **Live VMC Debugging**: Keep a `Print` or `Text Viz` branch on `UDP In.packet` while the main branch feeds `VMC Decoder`.
- **Multi-Model Selection**: Use `availableKeys` and `selectedKey` to lock onto the intended avatar when multiple identities are present.

## Pitfalls / Gotchas

- **Binary Input Required**: VMC is an OSC-based binary protocol, so keep the upstream `UDP In.outputMode` on `bytearray` for the main path.
- **Decoder Placement**: Put VMC-specific logic after `VMC Decoder`, not before; upstream nodes should stay transport-oriented.
- **No Legacy Wrapper**: Older graphs using `UDP VMC` should be migrated to `UDP In -> VMC Decoder`.

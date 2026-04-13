## When to Use

- `UDP VMC` is legacy documentation only. New graphs should not use this operator.
- Replace it with `UDP In -> VMC Decoder`.
- The transport/decode split is now the supported architecture for VMC ingest in PyEngine.

## Common Wiring Patterns

- **Migration Path**: Move UDP socket state such as `bindAddress`, `port`, and `reuseAddress` onto `UDP In`.
- **Decoder State**: Move VMC-specific selection and cleanup state onto `VMC Decoder`.
- **Downstream Wiring**: Reconnect existing skeleton consumers to `VMC Decoder.selectedSkeleton` or `VMC Decoder.skeletons`.

## Pitfalls / Gotchas

- **No Legacy Support**: Avoid keeping `UDP VMC` around as a compatibility alias in new docs or scenes.
- **Binary Input Mode**: `UDP In` should stay on `bytearray` for the main VMC path.
- **Decoder Boundary**: Put OSC/VMC-aware logic in `VMC Decoder`, not in the transport node.

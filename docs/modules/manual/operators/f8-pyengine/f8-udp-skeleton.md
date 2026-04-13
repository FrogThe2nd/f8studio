## When to Use

- `UDP Skeleton` is legacy documentation only. New graphs should not use this operator.
- Replace it with `UDP In -> Skeleton Decoder`.
- The transport/decode split is now the supported architecture for skeleton ingest in PyEngine.

## Common Wiring Patterns

- **Migration Path**: Move socket settings such as `bindAddress`, `port`, and `reuseAddress` onto `UDP In`.
- **Decoder State**: Move skeleton-specific settings such as `cleanupAfterMs` and `selectedKey` onto `Skeleton Decoder`.
- **Downstream Wiring**: Reconnect existing `skeletons` or `selectedSkeleton` consumers to `Skeleton Decoder`.

## Pitfalls / Gotchas

- **No Legacy Support**: Do not keep both styles in the same repo or graph; migrate fully to the split pipeline.
- **Binary Input Mode**: `UDP In` should typically use `bytearray` for skeleton packet decoding.
- **Doc Drift**: If you still see `UDP Skeleton` in older examples, treat those examples as outdated and update them to the split chain.

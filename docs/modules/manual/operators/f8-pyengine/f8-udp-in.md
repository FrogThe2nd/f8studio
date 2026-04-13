## When to Use

- Use `UDP In` as the generic ingress node for any UDP packet stream.
- It is the right starting point when the payload format is not yet decoded, or when multiple downstream consumers need access to the same packet metadata.
- Pair it with `Skeleton Decoder` or `VMC Decoder` for motion protocols instead of using legacy protocol-specific UDP nodes.

## Common Wiring Patterns

- **Binary Motion Stream**: Set `outputMode` to `bytearray`, then connect `packet` to `Skeleton Decoder.packet` or `VMC Decoder.packet`.
- **JSON Packet Ingest**: Set `outputMode` to `json` when the sender emits UTF-8 JSON payloads and wire `value` or `json` into downstream logic.
- **Debug Branch**: Attach `Text Viz` or `Print` to `text`, `json`, or `packet` while keeping protocol decoding on a separate branch.

## Pitfalls / Gotchas

- **Wrong Output Mode**: Binary protocols such as VMC and custom skeleton packets should use `bytearray`; `text` or `json` will only help for inspection.
- **Bind Security**: Non-loopback bind addresses stay blocked unless `allowNonLoopbackBind` is enabled explicitly.
- **Protocol Split**: `UDP In` does not decode motion payloads on its own anymore; decoding must happen in a dedicated downstream operator.

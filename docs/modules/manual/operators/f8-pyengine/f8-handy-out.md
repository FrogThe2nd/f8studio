#### When to Use

- Use `Handy Out` when the final output path targets The Handy device/protocol.
- It is the specialized sink for Handy-specific runtime control.

#### Common Wiring Patterns

- Feed it finalized `TCode` or equivalent control data after scaling and safety shaping are already done.
- Keep a viewer branch such as `TCodeViz` attached while validating device-targeted behavior.

#### Pitfalls / Gotchas

- Treat it as a transport/device layer, not the place to fix signal semantics.
- Verify device-specific cadence assumptions early in release testing.


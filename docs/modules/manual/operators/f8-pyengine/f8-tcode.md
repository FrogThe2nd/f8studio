#### When to Use

- Use `TCode` when one or more normalized control channels must become a TCode string stream.
- It is the core formatter between scalar motion signals and device/protocol outputs.

#### Common Wiring Patterns

- Feed it mapped numeric channels, then branch the resulting string to `Serial Out`, `Handy Out`, or `TCodeViz`.
- Keep timing explicit by aligning `intervalMs` with the tick source that drives updates.

#### Pitfalls / Gotchas

- If the upstream channel semantics are unclear, the final string may be valid but still wrong for the device.
- View the emitted string in `TCodeViz` before debugging transport nodes.


#### When to Use

- Use `Serial Out` when the final command stream should go to a serial-connected device.
- It is the simplest hardware sink for TCode-like outputs.

#### Common Wiring Patterns

- Feed it from `TCode` or another finalized string stream and keep `TCodeViz` in parallel during bring-up.
- Put safety/limiting nodes upstream, not in the serial node itself.

#### Pitfalls / Gotchas

- Port-name mistakes are far more common than graph-shape mistakes here.
- Validate the outgoing string format before blaming transport settings.


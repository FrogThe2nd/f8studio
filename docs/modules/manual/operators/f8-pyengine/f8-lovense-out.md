#### When to Use

- Use `Lovense Out` when the graph should drive a Lovense target directly from `f8.pyengine`.
- It is the device-facing sink for Lovense-specific runtime control.

#### Common Wiring Patterns

- Feed it already-mapped control values or Lovense-formatted program data from upstream adapters.
- Keep `Lovense Mock Server` or other debug aids around while validating release behavior.

#### Pitfalls / Gotchas

- Protocol/device assumptions should be validated before tuning intensity curves.
- Do not bury transport-specific fixes upstream if they only matter to this sink.


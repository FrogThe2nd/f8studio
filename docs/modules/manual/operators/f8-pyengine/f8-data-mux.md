## When to Use

- Use `Data Mux` when one low-frequency mode selects which named data input is
  exposed on a shared output.
- Pair it with `Exec Branch` for graphs that switch both control flow and data
  source using the same semantic mode.
- Use it to feed one normalization or output rack from several mutually
  exclusive motion strategies.

## Common Wiring Patterns

- **Mode Data Selection**: Connect each mode's value to a named input, set the
  selector state, and route the single output into shared processing.
- **Branch/Mux Pair**: Keep branch and mux selector values identical so exec
  flow and selected data cannot disagree.
- **Fallback Value**: Provide an explicit idle input rather than relying on a
  stale value from a previously active mode.

## Pitfalls / Gotchas

- The selector is configuration state, not per-frame telemetry. Use ordinary
  graph data flow for high-frequency switching.
- An unconnected selected input cannot provide valid fresh data. Downstream
  safety logic must treat that condition explicitly.
- `Data Mux` selects; it does not blend. Use a mixer operator when transitions
  must interpolate between sources.

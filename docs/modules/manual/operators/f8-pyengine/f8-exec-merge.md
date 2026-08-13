## When to Use

- Use `Exec Merge` to join mutually exclusive control-flow branches into one
  continuation.
- Use it after `Exec Branch` when all modes should eventually trigger the same
  downstream output stage.
- Keep it limited to control flow; data selection belongs in `Data Mux`.

## Common Wiring Patterns

- **Mode Rejoin**: Connect the terminal exec output of each exclusive branch to
  a merge input, then wire the single output to the shared continuation.
- **Shared Output Rack**: Merge several mode-specific preparation paths before
  one guarded device-output trigger.
- **Readable Layout**: Place it at the visual convergence point so branch
  ownership is obvious on the canvas.

## Pitfalls / Gotchas

- The node does not deduplicate simultaneous triggers. Its intended contract is
  mutually exclusive input branches.
- Merging exec flow does not merge or select data values. Pair it with an
  explicit `Data Mux` when downstream data also varies by mode.
- Preserve watchdog and arm gates after the merge when the continuation reaches
  physical output.

## When to Use

- Use `Exec Branch` when one low-frequency mode selection must route an exec
  event into exactly one branch.
- Prefer it over duplicating condition expressions across several trigger
  nodes; the selected branch remains visible in graph state.
- Pair it with `Exec Merge` when mutually exclusive branches later rejoin.

## Common Wiring Patterns

- **Mode Router**: Connect one trigger to `exec`, choose the branch in state,
  and wire each named exec output to one implementation path.
- **Safe Fallback**: Reserve one branch for an idle or disarmed path so every
  accepted mode has explicit behavior.
- **Branch And Rejoin**: Route outputs through separate processing nodes and
  connect their terminal exec outputs to one `Exec Merge`.

## Pitfalls / Gotchas

- The selector is low-frequency configuration state; do not rewrite it for
  every data frame.
- Only the selected output fires. Do not use this node when every branch must
  run; use `Sequence` for ordered fan-out.
- Keep branch labels and downstream purpose aligned so recipes remain readable.

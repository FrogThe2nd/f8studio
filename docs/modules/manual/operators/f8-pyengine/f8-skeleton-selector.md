## When to Use

- Use `Skeleton Selector` after `Skeleton Decoder` when several Unity
  characters are present and the graph must follow a stable semantic role.
- Prefer its `profileId`, `role`, and `roleIndex` fields over runtime model
  names, which can change as characters are loaded or renamed.
- Use one selector for the reference character and another for the target
  character before selecting individual bones.

## Common Wiring Patterns

- **Stable Pair**: Connect `Skeleton Decoder.skeletons` to two selectors, then
  send each `skeleton` output to its own `Bone Selector`.
- **Role Debugging**: Inspect the selector's data status alongside
  `Skeleton Decoder.skeletons` to distinguish a missing role from a missing
  bone.
- **Legacy Stream**: Set an exact `modelName` and explicitly enable legacy
  fallback only when consuming a pre-LMEX-v2 exporter.

## Pitfalls / Gotchas

- **No Fuzzy Identity**: The node does not guess roles from model names. A v2
  stream must match all three stable identity fields exactly.
- **Role Indices Start At Zero**: A second character with the same role uses
  `roleIndex=1`; it is not selected by a `roleIndex=0` node.
- **Legacy Fallback Is Explicit**: Enabling fallback without an exact model
  name remains invalid. Keep it disabled in new recipes.

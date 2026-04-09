# Debugging

Release readiness comes from being able to diagnose the same graph reliably inside Studio and the runtime services it deploys.

## Fast Debug Checklist

1. Compile the graph and inspect the generated runtime structure
2. Verify every operator host binding (`Service Id`)
3. Check required state fields for empty or invalid values
4. Confirm the producer/consumer SHM names actually match
5. Open a visualization node close to the failure stage, not only at the end

## Common Shortcuts

1. `Tab`: quick node search
2. `Delete` / `Backspace`: delete selected nodes
3. `Esc`: cancel placement
4. `Ctrl+R`: compile and print runtime graph
5. `F5`: deploy graph to runtime

Canvas navigation:

1. Middle mouse drag: pan
2. `W/A/S/D` or arrow keys: pan
3. `Q/E` or `PageUp/PageDown`: zoom

## Typical Failure Modes

- Ports refuse to connect because edge types differ
- Compile fails because a referenced host service does not exist
- Deploy fails because a required state field is missing or rejected
- UI nodes look healthy, but the runtime service itself is not running

## Cross-Checks

- [Scenarios](../scenarios/index.md)
- [Node Atlas](../node-atlas/index.md)

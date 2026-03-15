## When to Use

- Use `Sequence` when a single execution trigger (exec) needs to fan out into multiple branches that must run in a specific, ordered priority.
- It is the best tool for making evaluation order explicit on the Studio canvas, preventing "race conditions" between nodes.
- Use it to enforce a clear "Read -> Transform -> Write" pattern in your logic.

## Common Wiring Patterns

- **Standard Processing Flow**: Place a `Sequence` node immediately after a `Tick` or `Phase` trigger. Use output `0` for reading sensor data, output `1` for processing/remapping, and output `2` for sending commands to hardware.
- **Ordered Side Effects**: Use different numbered outputs for operations that depend on each other's results but shouldn't compete for resources in the same execution slice.
- **Initialization Helper**: Trigger a series of "reset" or "calibration" commands in a specific order when the scenario starts.

## Pitfalls / Gotchas

- **Shared Tick Load**: `Sequence` only controls the *order* of execution, not historical timing isolation. Expensive calculations on any branch will still stall the entire tick for all subsequent branches.
- **Implicit Complexity**: Overusing nested `Sequence` nodes can make the graph difficult to trace. Use descriptive names for your branches or use multiple `f8-pyengine` hosts if logic becomes too dense.
- **Port Naming**: The outputs are strictly ordered (0, 1, 2...). Ensure your most critical "upstream" logic is always on a lower-numbered port than the nodes that depend on its output.

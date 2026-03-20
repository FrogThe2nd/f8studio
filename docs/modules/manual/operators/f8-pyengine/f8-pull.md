## When to Use

- Use `Pull` primarily as an advanced internal helper when your graph requires an explicit "auto-sampling" sink for an upstream data node that doesn't have its own execution trigger.
- It is useful for bridging between "passive" data providers (nodes that only update when asked) and "active" control consumers.
- Use it sparingly; most graphs should rely on clear, explicit execution triggers (like `Tick`).

## Common Wiring Patterns

- **Evaluation Forcing**: Use it to force periodic evaluation of an upstream expression or service port in a graph that lacks a `Tick` source.
- **Data Sampling**: Place it at the end of a long chain of data-only nodes to ensure the entire chain is sampled at a specific frequency.
- **Internal Helper**: Use it when creating custom operator groups where a group-internal state needs to be updated without exposing an execution port to the outside.

## Pitfalls / Gotchas

- **Hidden Execution Model**: Relying on `Pull` can make the graph's execution flow opaque to other developers. It is always better to use explicit `exec` wires from a `Tick` node when possible.
- **Resource Overhead**: Since `Pull` samples autonomously, it can lead to redundant processing if multiple `Pull` nodes are attached to the same data sources.
- **Drift**: Autonomous sampling may not be perfectly synchronized with the rest of your graph's heartbeat. Use `Tick` for anything where phase-perfect timing matters.

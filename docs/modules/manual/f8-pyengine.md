## When to Use

- Use `f8.pyengine` as the primary runtime host for executing `f8.*` operators, signal transforms, protocol adapters, and custom business logic.
- It is the flexible orchestration hub of the Feel8 ecosystem, allowing you to compose complex logic from simple, reusable operators.
- Choose `PyEngine` when your graph needs to process data streams, handle events, or manage state through composable operator chains.

## Common Wiring Patterns

- **Operator Hosting**: Create one or more `PyEngine` host nodes in the Studio. Each operator in the session must have its `Service Id` property set to the ID of a running `PyEngine` host.
- **Logical Segregation**: Use separate `PyEngine` hosts for different functional domains (e.g., one for "Vision Processing", one for "Device Control") to improve performance isolation and simplify debugging.
- **Signal Flow**: Standard chains usually follow: `Consumer Port` -> `Operator (Transform)` -> `Producer Port`. Use internal signals for light-weight data passing within the engine.

## Pitfalls / Gotchas

- **Service ID Mapping**: If an operator graph looks correct but doesn't produce output or react to inputs, verify that the operator's `Service Id` matches the `PyEngine` host node id exactly.
- **State Blocking**: Avoid long-running or blocking code in custom operators, as it can stall the entire `PyEngine` event loop. Use background threads or async patterns if necessary.
- **Resource Management**: Large graphs with many operators on a single host can consume significant CPU. Monitor the host's performance via the `monitor` port and split the workload across multiple `PyEngine` instances if latency increases.

## When to Use

- Use `f8.pyengine` as the main host for operator-driven logic inside Feel8 graphs.
- It is the standard place for chaining transforms, triggers, state machines, and custom orchestration logic.
- Once a graph needs a real logic layer, `PyEngine` is usually central to it.

## Common Wiring Patterns

- Start by placing one or more `PyEngine` service nodes.
- Every hosted operator must point its `Service Id` at the intended engine host.
- As graphs grow, split engines by purpose, such as one for vision and one for device control.

## Pitfalls / Gotchas

- If operators appear correctly wired but never run, check `Service Id` first.
- Long-running or blocking logic inside one engine can stall the entire host.
- When one host becomes too large or latency grows, split the workload rather than continuing to pile onto one engine.

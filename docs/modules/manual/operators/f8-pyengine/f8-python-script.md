## When to Use

- Use the `Python Script` operator when a specific part of your graph needs bespoke logic that should still execute inside the high-performance `f8.pyengine` environment.
- It is the primary "escape hatch" for complex signal processing, domain-specific algorithms, or orchestrating flow between multiple operators.
- Choose this when you need to maintain internal state across multiple data frames (e.g., counters, moving averages, or state machines).

## Common Wiring Patterns

- **Modular Logic**: Keep the script narrow in scope. Instead of one massive script for the whole scene, use multiple script nodes each handling a single responsibility (e.g., "Hand Gesture Logic," "Sequence Orchestration").
- **Inspection Layers**: Surround your script node with `f8-viz-text` and `f8-viz-wave` nodes so its internal inputs and outputs stay obvious during debugging.
- **Dynamic Port Scaling**: Define your custom input and output ports in the node properties to make the script's interface explicit on the graph canvas.

## Pitfalls / Gotchas

- **Maintenance Bottleneck**: A script node can become a "black box" that hides too much logic from the visual graph representation. Always document your code and keep the script's external interface clear.
- **Port Naming**: Avoid using generic names like `input1` or `output1`. Use semantic names (e.g., `target_velocity`, `is_active`) to make the data flow readable to others.
- **Blocking Calls**: Never perform blocking I/O (like `time.sleep()` or synchronous requests) inside the script's processing callback, as this will stall the entire `PyEngine` thread and lock up your graph.
- **State Leaks**: Be careful with persistent variables; ensure your script handles initialization and reset logic properly when the graph starts or stops.

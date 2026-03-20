## When to Use

- Use `f8.pyscript` when you need to run custom Python logic as a standalone, persistent service with its own lifecycle, ports, and state.
- It is the most flexible integration point for the Feel8 platform, allowing you to bridge between external APIs, manage complex multi-node orchestration, or implement logic that requires persistent internal state.
- Choose this when `PyEngine` operators are too fine-grained and `PyExpr` is too simple for your requirements.

## Common Wiring Patterns

- **Integration Bridge**: Use it to listen to data ports, process them through high-level libraries (like OpenCV or custom ML models), and then emit new data or command signals.
- **Scenario Orchestrator**: Write a script that monitors global graph health (via `monitor` ports) and automatically restarts or reconfigures other services when specific conditions are met.
- **Declarative Schema**: Define your script's input/output ports and state fields in its configuration to ensure Studio can provide autocomplete and visualization for its connections.

## Pitfalls / Gotchas

- **Blocking the Loop**: Like all Python services, a long-running calculation or a synchronous network request in the main thread will block your service's data processing. Use the `asyncio` loop or background threads for IO-heavy operations.
- **Opaque Logic**: Scripts that grow too large can become "black boxes" that are hard for others to understand or debug. Consider breaking very complex scripts into smaller `f8.pyengine` operators if the logic is reusable.
- **Input Error Handling**: Scripts often fail silently or crash when receiving unexpected data types. Always include robust `try/except` blocks at your data entry points to log errors without stopping the service.

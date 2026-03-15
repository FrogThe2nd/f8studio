## When to Use

- Use the `Control Panel` operator when you want a custom, lightweight Studio-local UI surface for tweaking specific graph values during testing and authoring.
- It is ideal for live tuning sessions where sliding a fader or clicking a toggle is more intuitive than editing raw JSON properties.
- Use it to build "operator dashboards" for a scenario, allowing a non-technical user to safely adjust parameters without touching the node graph.

## Common Wiring Patterns

- **Targeted Tuning**: Pair it with runtime service nodes that expose meaningful state fields (like `thresholds` or `scales`). Bind the panel controls to these fields to adjust them in real-time.
- **Scenario Master**: Keep a master control panel at the start of your scenario to handle global settings like "Debug Mode," "Master Volume," or "Active Mode."
- **Feedback Loop**: Position the panel near the visualizer for the signal it controls so you can see the results of your adjustments immediately.

## Pitfalls / Gotchas

- **Editor-Only Aid**: The control panel is a Studio tool. It is not a substitute for clear runtime defaults. Ensure your nodes have sane default values for when they run without a Studio connection.
- **State Confusion**: Overusing custom controls can make it hard to track which node "owns" a particular piece of state. Always label your controls clearly.
- **Persistence**: While the panel state is saved in the session, ensure that mission-critical settings are moved into persistent service configurations once they are finalized.

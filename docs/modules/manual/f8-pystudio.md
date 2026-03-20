## When to Use

- Use `f8.pystudio` for editor-local operators that exist purely to assist with graph authoring, inspection, annotation, or manual interaction inside the Feel8 Studio UI.
- It is the built-in host for nodes like "Stickies," "Previews," and "Manual Controls" that provide developer feedback but do not participate in the final deployed runtime.
- Use it during the "authoring phase" to explain complex branches, capture reference data, or provide manual overrides during testing.

## Common Wiring Patterns

- **Live Inspection**: Attach Studio-local preview nodes to capture/analysis branches to verify data flow without needing to deploy a heavy visualization service.
- **Narrative Authoring**: Use "Notes" or "Group" operators to organize your graph and explain the logic to future readers or your future self.
- **Manual Debugging**: Connect manual "Slider" or "Button" operators to a running `f8.pyengine` graph to test how your logic reacts to specific value ranges.

## Pitfalls / Gotchas

- **Editor-Only Scope**: `f8.pystudio` operators do NOT run on remote nodes or in standalone deployments. Any logic critical for the scenario's behavior MUST be implemented in a deployable service node (like `f8.pyengine`).
- **Dependency confusion**: Graphs that rely on Studio-local logic for their core function will fail when run in "Headless" or "Release" modes. Always verify that your scenario works with Studio closed or disconnected.
- **Performance Impact**: While separate from the runtime engine, complex visualization nodes in the editor still use local CPU/Memory. Avoid creating hundreds of local previews in one session.

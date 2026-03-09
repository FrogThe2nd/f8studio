## When to Use

- Use `f8.cvkit.templatematch` when the target appearance is stable and you need quick region matching.
- It is a strong fit for UI markers, anchored screen regions, or controlled camera scenes.

## Common Wiring Patterns

- Pair it with `f8.screencap` or `f8.implayer`, then inspect results through `f8.viz.track`.
- Use the repo-local `template_match_capture` plugin workflow to acquire the initial template directly inside Studio.

### Template Capture Workflow

- Initialize the template from Studio, then keep the runtime service and tracking visualization nodes in the graph.
- Re-capture only when the target appearance changes enough to invalidate the original template.

## Pitfalls / Gotchas

- Poor initial template quality is the fastest way to get drift and low confidence.
- Large scale or rotation changes are usually a sign to switch to a different tracking strategy.


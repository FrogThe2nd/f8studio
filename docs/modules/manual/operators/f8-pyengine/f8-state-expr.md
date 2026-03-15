#### When to Use

- Use `State Expr` when a computed value should be derived from editable state fields instead of incoming data ports.
- It works well for formulas, derived parameters, and lightweight control math that should stay visible on the node itself.

#### Common Wiring Patterns

- Add explicit numeric state fields for the symbols you want to expose, then publish the computed `out` state into downstream state edges or inspector views.
- Keep the expression focused on a small set of clearly named fields so the node remains easy to tune live.

#### Pitfalls / Gotchas

- Only writable numeric state fields become expression symbols automatically, so non-numeric or protected fields will not participate in evaluation.
- Failed expressions publish `lastError` and clear the output, which is helpful for debugging but can make downstream state-driven graphs look idle if you miss the error field.

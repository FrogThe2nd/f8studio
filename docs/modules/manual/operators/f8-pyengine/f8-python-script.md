#### When to Use

- Use `Python Script` when one operator in the graph needs custom logic but should still live inside `f8.pyengine`.
- It is the most flexible in-graph escape hatch for shaping data or control flow.

#### Common Wiring Patterns

- Keep the script narrow in scope and surround it with visualization nodes so its inputs and outputs stay obvious.
- Prefer one script per clear responsibility instead of one script doing the whole scenario.

#### Pitfalls / Gotchas

- A script node can become a maintenance bottleneck if it hides too much graph logic.
- Weak schemas around the script make completion and downstream debugging much worse.


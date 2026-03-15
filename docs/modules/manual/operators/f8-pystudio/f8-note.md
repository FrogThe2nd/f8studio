## When to Use

- Use the `Note` operator when your graph needs durable, inline explanations, "todo" lists, or scenario-specific setup instructions directly on the canvas.
- It is the primary tool for documenting your intent for other developers (or your future self) without relying on external READMEs.
- Use it to highlight critical property settings or explain why a particular non-obvious wiring pattern was used.

## Common Wiring Patterns

- **Instructional Context**: Place large notes at the "entry" of a scenario to explain what it does and what hardware is required.
- **Local Documentation**: Place small notes beside tricky branches, deployment assumptions, or temporary workarounds to make future maintenance safer.
- **Group Labeling**: Use notes as labels for different logical zones of your graph (e.g., "SECTION: AUDIO PROCESSING").

## Pitfalls / Gotchas

- **Stale Content**: Notes only help if they stay current. Outdated notes describing logic that has since changed are worse than having no notes at all.
- **Structural Integrity**: Notes are purely visual aids. Critical runtime constraints should be represented in the graph structure or code, not exclusively in prose.
- **Canvas Clutter**: Avoid turning the graph into a "wall of text." Keep notes concise and close to the specific nodes they describe.

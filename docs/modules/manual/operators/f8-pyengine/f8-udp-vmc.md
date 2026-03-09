#### When to Use

- Use `UDP VMC` when the graph should ingest VMC/OSC pose streams directly.
- It is a strong entrypoint for live avatar or mocap-driven skeleton workflows.

#### Common Wiring Patterns

- Feed `selectedSkeleton` into `Bone Selector`, `Bone Filter`, or `f8.viz.three_d`.
- Keep `availableKeys` and `selectedKey` visible while choosing which model stream the graph should follow.

#### Pitfalls / Gotchas

- Network bind settings and selected-key mismatches are the first things to verify.
- Coordinate-frame or source-quality issues should be visualized before downstream mapping is tuned.


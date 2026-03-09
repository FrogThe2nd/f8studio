#### When to Use

- Use `UDP Skeleton` when skeleton payloads arrive over a simple UDP path and should enter the graph as pose data.
- It is a straightforward ingest node for external body-tracking sources.

#### Common Wiring Patterns

- Feed its skeleton output into `Bone Selector`, `Bone Filter`, or `f8.viz.three_d`.
- Keep a visualization branch attached while validating source coordinate conventions.

#### Pitfalls / Gotchas

- Networking and coordinate-frame issues often look like downstream logic bugs.
- Do not tune bone-processing nodes until the incoming skeleton itself is visually sane.


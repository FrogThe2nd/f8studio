#### When to Use

- Use `Quat To Euler` when quaternion rotation data must become human-readable or axis-specific angles.
- It is most useful at the edge between skeleton math and scalar control logic.

#### Common Wiring Patterns

- Feed it from `Bone Selector` or `Bone Filter`, then map the resulting Euler components into waves or device outputs.
- Keep the chosen order and degree/radian choice explicit in notes or node labels.

#### Pitfalls / Gotchas

- Wrong rotation order can produce believable but incorrect motion.
- Euler conversion should happen as late as possible if upstream nodes can stay in quaternion form.


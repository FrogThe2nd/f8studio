#### When to Use

- Use `Wave Funscript` when motion should come from an authored `.funscript` file rather than a synthetic expression or hand-entered point list.
- It is the right choice when you want repeatable playback of an existing script axis inside a `f8.pyengine` graph.

#### Common Wiring Patterns

- Point the node at a `.funscript`, select the desired axis, drive `t` from playback time, and route the normalized `value` output into downstream motion or device nodes.
- Start with linear interpolation for faithful playback, then experiment with smoother interpolation only if the authored material benefits from it.

#### Pitfalls / Gotchas

- File path, axis selection, and inferred duration all affect the resulting loop, so a valid script can still behave unexpectedly if it targets the wrong axis or timing source.
- Smoother interpolation modes can change the feel of authored actions; if exact script intent matters, verify the preview against the original source before relying on it.

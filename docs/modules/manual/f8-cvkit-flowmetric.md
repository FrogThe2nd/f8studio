## When to Use

- Use `f8.cvkit.flowmetric` when dense optical flow needs to be reduced into a scalar field or metric map.
- It is useful when downstream logic needs divergence, magnitude, curl, or strain rather than raw vectors.

## Common Wiring Patterns

- Feed it from `f8.cvkit.denseoptflow`, then visualize or normalize the result with `f8.viz.video`, `f8.viz.wave`, and `Range Map`.
- Use it as the bridge between video motion analysis and scalar-control pipelines.

## Pitfalls / Gotchas

- `inputFlowShmName` must match the flow producer exactly; this is the first thing to verify.
- The metric can look weak or noisy if scale settings are tuned before the flow source itself is validated.


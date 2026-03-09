# Node Categories and Wiring

Studio graphs normally mix three kinds of nodes.

## Node Categories

1. `Service Node`: a runtime service instance such as `f8.implayer`, `f8.cvkit.tracking`, or `f8.pyengine`
2. `Operator Node`: logic hosted by a service, most often `f8.pyengine.*`
3. `UI Node`: editor-local visualization/control helpers such as `f8.viz.wave` or `f8.control_panel`

## Wiring Rules

- `Exec` edges drive ordered control flow between compatible execution ports.
- `Data` edges carry values between typed input/output ports.
- `State` edges mirror values into state fields and are useful for exposing a small control surface.
- Operators that belong to container services must point `Service Id` at the target host service node `id`.
- UI nodes never replace runtime consumers; use them to observe, inspect, or inject values during authoring.

## Practical Patterns

- Use `Tick -> Sequence -> processing chain` when you need deterministic periodic execution.
- Use `State` edges plus `ControlPanel` when one value should feed several downstream state fields.
- Place visualization nodes close to the signal stage they explain, not only at the pipeline output.
- Keep service node ids stable once scenarios are shared, because many operator references depend on them.

## Connection Failures To Check First

1. Wrong edge type: `exec`, `data`, and `state` are not interchangeable
2. Missing operator `Service Id`
3. Incompatible schemas or mismatched semantics even when the schema says `any`
4. Disabled or missing service node that the operator expects to run inside

## Canonical References

- [Node Atlas](../node-atlas/index.md)
- [Modules Overview](../modules/index.md)


# Service Bus Architecture

## Status

This document describes the current `f8pysdk.service_bus` runtime shape after Slice A through Slice D.

The implementation is still compatibility-heavy, but the runtime is now split more explicitly:

- `ServiceBus` is the public façade.
- command dispatch lives behind `CommandGateway`
- data routing/buffering/subscriptions live behind `DataRouter`
- state cache/access/read semantics live behind `StateStore`
- intra-service state routing and cross-state watch lifecycle live behind `StateRouter`
- several top-level modules are compatibility re-export layers

The long-term plan is tracked in `packages/f8pysdk/SDK_REFACTOR_PLAN.md`.

## Layered Modules

- `api/`: public façade and config
- `domain/`: state validation, normalization, persistence, local delivery
- `routing/`: data buffering, pull/push delivery, NATS data subscriptions
- `workflow/`: rungraph apply, lifecycle transitions, cross-state synchronization
- `adapters/`: transport-specific endpoint integration

Slice D notes:

- `routing/data_router.py` is now the canonical owner for:
  - data route tables
  - input buffers
  - routed and custom subscriptions
  - push-callback micro-batching
- `state_store.py` is now the canonical owner for:
  - local state cache
  - per-node state access map
  - KV-backed state read path
- `state_router.py` is now the canonical owner for:
  - intra-service state-edge fanout tables
  - cross-service state bindings
  - remote state watch handles
  - cross-state target tracking and remote timestamp ordering
- `routing_data.py` remains as a thin compatibility layer.
- `ServiceBus` delegates data emit/pull/subscribe behavior to `DataRouter` instead of storing that mutable state directly.
- `workflow/cross_state.py` is now a thin compatibility wrapper over `StateRouter`.

## Current Contracts

### Lifecycle

- `ServiceBus` is a single-run object.
- `ServiceRuntime` is a single-run object.
- After `stop()`, callers should create a new instance instead of calling `start()` again.

### Registry

- `ServiceCliTemplate.build_registry()` should return a fresh registry by default.
- Shared registries are explicit opt-in.
- Passing an explicit registry instance is the supported way to share registrations across components in one process.

## Canonical Chains

### State Write Chain

1. caller invokes `publish_state_external(...)` or `publish_state_runtime(...)`
2. `domain/state_pipeline.publish_state(...)` validates access and value
3. state is persisted to KV
4. local delivery runs immediately for same-process writes
   runtime propagation controls are carried by typed `StatePublishOptions`, not magic `meta` flags
5. local delivery triggers:
   - hidden command dispatch for hidden command input fields
   - `node.on_state(...)` for normal state fields
   - intra-service state-edge fanout
6. cross-service state propagation is handled separately by remote KV watch in `workflow/cross_state.py`

### Command Invoke Chain

There are still two command entry adapters, but they now share one `CommandGateway` execution core:

1. hidden command-state adapter
   - state write to hidden command input field
   - `dispatch_command_input(...)`
   - argument normalization via `map_command_args(...)`
   - `execute_command(...)`
2. micro request/reply adapter
   - NATS micro `cmd` endpoint
   - request decode / compatibility parsing
   - `execute_command(...)` / `CommandGateway.invoke(...)`
   - direct reply payload

Shared execution behavior:

- node lookup and commandability check
- declared-command argument normalization
- `node.on_command(...)` invocation
- error classification
- hidden command output state writeback
- explicit output policy via typed command invoke options

SDK-facing local façade:

- `ServiceBus.invoke_command(node_id, call, args, ...)` now provides one explicit local command entrypoint for SDK users
- `ServiceBus.invoke_command(...)` is reply-first by default; hidden output writeback is explicit opt-in
- adapters still exist for compatibility, but they no longer define their own execution semantics

Current compatibility note:

- hidden command-state input remains the graph-compatibility adapter
- declared commands accept scalar/list/dict on both hidden-state and request/reply command paths
- undeclared commands on request/reply paths require object-shaped args because there is no param schema to map positional values
- micro `_cmd` is reply-first and no longer performs hidden output writeback
- hidden output writeback failure is logged once and does not fail the command itself

### Data Emit/Pull Chain

1. `emit_data(...)` delegates to `DataRouter.emit_data(...)`
2. local samples are delivered to intra-service targets first
   internal propagation controls are carried by typed `DataEmitOptions`, not ad hoc branching
3. local delivery mode is explicit:
   - `callback`: `on_data(...)` only
   - `buffered`: `pull_data(...)` only
   - `both`: explicit dual local delivery for compatibility
4. cross-service publication is controlled separately by `cross_publish_policy`:
   - `routed`: publish only when the rungraph has a cross-service outgoing edge
   - `all`: publish every emitted output subject
   - `none`: never publish cross-service data
5. pull-based local computation may call `compute_output(...)` upstream, but it now does so through explicit local-only emit options and does not implicitly cross-publish

### Rungraph Apply Chain

1. `set_rungraph(...)` timestamps and validates the graph
2. routing tables and state-access maps are rebuilt
3. `DataRouter.replace_routes(...)` swaps the live data-side route state
4. `StateRouter.replace_intra_state_routes(...)` swaps the live state-side route state
5. rungraph `stateValues` are materialized into KV
6. builtin identity state is seeded
7. rungraph hooks execute
8. cross-state watches sync remote values through `StateRouter`
9. intra-service initial state-edge propagation runs

## Compatibility Surface

These modules currently exist mostly as compatibility shims:

- `bus.py`
- `cross_state.py`
- `lifecycle.py`
- `micro.py`
- `routing_data.py`
- `rungraph_apply.py`
- `state_publish.py`

New code should prefer the documented façade types and avoid depending on compatibility modules unless migration constraints require it.

Stable top-level modules introduced during Slice D:

- `f8pysdk.codec`
- `f8pysdk.state`
- `f8pysdk.testing`

Stable top-level modules introduced during public API cleanup:

- `f8pysdk.app`
- `f8pysdk.command`
- `f8pysdk.data`
- `f8pysdk.nodes`
- `f8pysdk.registry`
- `f8pysdk.transport`

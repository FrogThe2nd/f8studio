# Service Bus Architecture

## Status

This document describes the current `f8pysdk.service_bus` runtime shape after Slice A through Slice D.

The runtime is split into explicit owners:

- `ServiceBus` is the public façade.
- command dispatch lives behind `CommandGateway`
- data routing/buffering/subscriptions live behind `DataRouter`
- state cache/access/read semantics live behind `StateStore`
- intra-service state routing and cross-state watch lifecycle live behind `StateRouter`
- a few top-level modules intentionally remain as ergonomic SDK entrypoints

The long-term plan is tracked in `packages/f8pysdk/SDK_REFACTOR_PLAN.md`.

## Layered Modules

- `data/`: data routing, buffering, local delivery, and cross-service data fanout
- `state/`: state cache, routing, validation, persistence, and state-side helper ownership
- `workflow/`: rungraph apply, lifecycle transitions, cross-state synchronization
- `internal/`: non-public typed command/data/runtime infrastructure helpers

The runtime is Zenoh-first through the explicit `RuntimeTransport` protocol.
State is service-owned:
each service keeps its latest local state snapshot, exposes it through the
transport KV/query facade, and publishes state update samples for watchers.

Slice D notes:

- `data/router.py` is now the canonical owner for:
  - data route tables
  - input buffers
  - routed and custom subscriptions
  - push-callback micro-batching
- `data/emit.py` now owns typed per-sample data propagation controls.
- `data/flow.py` is the functional adapter layer over `DataRouter`.
- `state/store.py` is now the canonical owner for:
  - local state cache
  - per-node state access map
  - RuntimeTransport-backed state read path
- `state/router.py` is now the canonical owner for:
  - intra-service state-edge fanout tables
  - cross-service state bindings
  - remote state watch handles
  - cross-state target tracking and remote timestamp ordering
- `state/pipeline.py` now owns state validation, normalization, persistence, and local state delivery.
- `state/helpers.py` now owns state-side metadata and inbound timestamp coercion helpers.
- `state/options.py` now owns typed state publish controls.
- `workflow/metadata.py` now owns rungraph/lifecycle metadata builders.
- `internal/cache.py` and `internal/logging.py` now own shared runtime infrastructure helpers.
- `ServiceBus` delegates data emit/pull/subscribe behavior to `DataRouter` instead of storing that mutable state directly.
- `workflow/cross_state.py` remains a focused helper for remote state sync lifecycle.

## Current Contracts

### Lifecycle

- `ServiceBus` is a single-run object.
- `ServiceRuntime` is a single-run object.
- After `stop()`, callers should create a new instance instead of calling `start()` again.

### Registry

- Service entrypoints should own a fresh `Registry()` / `ServiceApp(...)` by default rather than depending on process-global registration state.
- Shared registries are explicit opt-in.
- Passing an explicit registry instance is the supported way to share registrations across components in one process.

### Configuration

- `ServiceBusConfig` is the canonical runtime configuration core for service id/class, transport, routing, data delivery, cache limits, and monitoring.
- `ServiceRuntimeConfig` composes `bus: ServiceBusConfig` with registry module loading only.
- `ServiceHost` derives its service class from `ServiceBusConfig.service_class` unless a focused `ServiceHostConfig` override is passed for direct host tests or low-level composition.

## Canonical Chains

### State Write Chain

1. caller invokes `publish_state_external(...)` or `publish_state_runtime(...)`
2. `state/pipeline.publish_state(...)` validates access and value
3. state is persisted through the RuntimeTransport state facade
4. local delivery runs immediately for same-process writes
   runtime propagation controls are carried by typed `StatePublishOptions`, not magic `meta` flags
5. local delivery triggers:
   - hidden command dispatch for hidden command input fields
   - `node.on_state(...)` for normal state fields
   - intra-service state-edge fanout
6. cross-service state propagation is handled separately by retained remote state watch in `state/router.py`

### Command Invoke Chain

There are still two command entry adapters, but they now share one `CommandGateway` execution core:

1. hidden command-state adapter
   - state write to hidden command input field
   - `dispatch_command_input(...)`
   - argument normalization via `map_command_args(...)`
   - `execute_command(...)`
2. request/reply control endpoint adapter
   - Zenoh command-stream `cmd` endpoint
   - request decode / argument validation
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
- both adapters remain because they are distinct current entry protocols, but they no longer define their own execution semantics

Current command note:

- hidden command-state input remains the graph command adapter
- declared commands accept scalar/list/dict on both hidden-state and request/reply command paths
- undeclared commands on request/reply paths require object-shaped args because there is no param schema to map positional values
- request/reply `cmd` is reply-first and no longer performs hidden output writeback
- hidden output writeback failure is logged once and does not fail the command itself

### Data Emit/Pull Chain

1. `emit_data(...)` delegates to `DataRouter.emit_data(...)`
2. local samples are delivered to intra-service targets first
   internal propagation controls are carried by typed `DataEmitOptions`, not ad hoc branching
3. local delivery mode is explicit:
   - `callback`: `on_data(...)` plus the current local input buffer
   - `buffered`: `pull_data(...)` only
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
5. rungraph `stateValues` are materialized into the service-owned state facade
6. builtin identity state is seeded
7. rungraph hooks execute
8. cross-state watches sync remote values through `StateRouter`
9. intra-service initial state-edge propagation runs

## Public And Internal Surface

The old deep `service_bus.*` compatibility shims have now been removed from the
repo. New code should use stable SDK modules and owner packages directly:

- public bus/runtime entrypoints: `f8pysdk.service_bus`, `f8pysdk.app`
- public protocol/type modules: `f8pysdk.command`, `f8pysdk.data`, `f8pysdk.state`
- stable helpers: `f8pysdk.codec`, `f8pysdk.testing`, `f8pysdk.monitoring`
- canonical service-bus owners: `f8pysdk.service_bus.runtime`,
  `f8pysdk.service_bus.config`
- owner packages for runtime internals: `f8pysdk.service_bus.data.*`,
  `f8pysdk.service_bus.state.*`, `f8pysdk.service_bus.workflow.*`,
  `f8pysdk.service_bus.internal.*`

Stable top-level modules introduced during Slice D:

- `f8pysdk.codec`
- `f8pysdk.state`
- `f8pysdk.testing`

Stable top-level modules introduced during public API cleanup:

- `f8pysdk.app`
- `f8pysdk.command`
- `f8pysdk.data`
- `f8pysdk.monitoring`
- `f8pysdk.nodes`
- `f8pysdk.registry`
- `f8pysdk.transport`

Explicit internal boundary introduced during public API cleanup:

- `f8pysdk.service_bus.internal.*` for repo-internal tests and runtime helpers
  that still need typed access to non-public behavior without depending on ad
  hoc deep module paths
- `f8pysdk.service_bus.data.*` for data-runtime owner modules; the old
  `routing/*` compatibility namespace has been removed
- `f8pysdk.service_bus.state.*` for state-runtime owner modules that should not
  sit at the public `service_bus` root
- `runtime.py` and `config.py` now hold the canonical owner paths behind the
  stable `f8pysdk.service_bus` entrypoint
- older thin API facades under `service_bus.api.*` and root thin state facade
  modules have been removed
- internal publish controls live in `state.options`
- legacy compatibility shells have been removed; imports should point at
  explicit owner modules such as `data.emit`, `data.router`, `data.flow`,
  `internal.control_endpoints`, `internal.micro`, `state.options`, `state.pipeline`, `state.router`, and
  `state.store`

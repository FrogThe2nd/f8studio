# Service Bus Architecture

## Status

This document describes the current `f8pysdk.service_bus` runtime shape after Slice A and Slice B hardening.

The implementation is still compatibility-heavy:

- `ServiceBus` is the public façade.
- most runtime state still lives on `ServiceBus`
- submodules cooperate through `bus._...` fields
- several top-level modules are compatibility re-export layers

The long-term plan is tracked in `packages/f8pysdk/SDK_REFACTOR_PLAN.md`.

## Layered Modules

- `api/`: public façade and config
- `domain/`: state validation, normalization, persistence, local delivery
- `routing/`: data buffering, pull/push delivery, NATS data subscriptions
- `workflow/`: rungraph apply, lifecycle transitions, cross-state synchronization
- `adapters/`: transport-specific endpoint integration

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

1. `emit_data(...)` delivers local samples to intra-service targets first
2. local delivery mode is explicit:
   - `callback`: `on_data(...)` only
   - `buffered`: `pull_data(...)` only
   - `both`: explicit dual local delivery for compatibility
3. cross-service publication is controlled separately by `cross_publish_policy`:
   - `routed`: publish only when the rungraph has a cross-service outgoing edge
   - `all`: publish every emitted output subject
   - `none`: never publish cross-service data
4. pull-based local computation may call `compute_output(...)` upstream, but it now satisfies local targets only and does not implicitly cross-publish

### Rungraph Apply Chain

1. `set_rungraph(...)` timestamps and validates the graph
2. routing tables and state-access maps are rebuilt
3. rungraph `stateValues` are materialized into KV
4. builtin identity state is seeded
5. rungraph hooks execute
6. cross-state watches sync remote values
7. intra-service initial state-edge propagation runs

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

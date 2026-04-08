# f8pysdk SDK Strengthening Plan

## Goal

Turn `f8pysdk` from a feature-rich but tightly coupled runtime SDK into a smaller, more explicit, more testable SDK with:

- one clear command path
- one explicit data delivery model per node
- one predictable state propagation model
- explicit runtime/registry lifecycle
- a narrow, documented public API surface

This plan is intentionally incremental. Each phase should leave the SDK usable and releasable.

## Design Principles

- Prefer explicit behavior over implicit fallback.
- Prefer typed option objects over magic `meta` flags.
- Prefer one canonical path per capability.
- Preserve compatibility only through thin adapters, not duplicated logic.
- Fail fast on missing registration or invalid topology.
- Keep public API small; move compatibility imports behind clearly marked boundaries.

## Current Weak Points

- Data outputs are over-published by default, even for purely local graphs.
- Commands currently have two execution paths: hidden state command dispatch and micro request/reply command dispatch.
- `data_delivery="both"` creates dual-consumption semantics for the same sample.
- `ServiceBus.stop()` clears hook registrations, which makes restart/reuse semantics fragile.
- `RuntimeNodeRegistry` and `ServiceCliTemplate` lean on process-global singleton behavior.
- Missing operator runtime factories can silently degrade into generic nodes.
- Service runtime factory behavior is still partly compatibility-driven and needs explicit docs/tests.
- Layered module structure exists on paper, but runtime state still lives in one large mutable `ServiceBus`.
- Public API boundaries are blurry; sibling packages import internal modules directly.

## Delivery Strategy

- Phase 0 and Phase 1 are foundation phases and should happen first.
- Phase 2 and Phase 3 remove the largest semantic duplication.
- Phase 4 and Phase 5 harden lifecycle and long-term maintainability.
- Phase 6 is the public cleanup and deprecation pass after internals stabilize.

## Phase 0: Baseline and Safety Net

### Objectives

- Freeze the current behavior with targeted regression tests.
- Make the known weak spots visible before changing behavior.

### Checklist

- [x] Add a regression test for redundant cross-publish when `publish_all_data=True` and no cross-service subscribers exist.
- [x] Add a regression test that demonstrates current dual-delivery behavior for `data_delivery="both"`.
- [x] Lock in lifecycle behavior by documenting and testing the current non-restartable `ServiceBus` / `ServiceRuntime` contract.
- [x] Add a regression test for singleton registry contamination across repeated `describe_json()` or repeated CLI use.
- [x] Add a regression test for missing operator factory fallback to generic `OperatorNode`.
- [x] Add a focused test that hidden-state command path and micro command path produce different argument semantics today.
- [x] Add a short architecture note mapping the current canonical chains:
  - state write chain
  - command invoke chain
  - data emit/pull chain
  - rungraph apply chain

### Acceptance

- We can point to a failing or passing test for every major weakness already identified.
- We know exactly which behavior is intentional compatibility and which behavior we plan to change.

## Phase 1: Make Runtime Semantics Explicit

### Objectives

- Reduce semantic ambiguity without large structural rewrites yet.
- Introduce typed control objects where behavior is currently hidden in ad hoc flags.

### Checklist

- [x] Introduce a typed internal `StatePublishOptions` dataclass to replace magic propagation control keys like `_noStateFanout`.
- [x] Introduce a typed internal `CommandInvokeOptions` dataclass for command source/result policy metadata.
- [x] Introduce a typed internal `DataEmitOptions` or `CrossPublishPolicy` abstraction.
- [x] Move compatibility-only flags and hidden metadata shaping behind helper constructors instead of open-coded dict assembly.
- [x] Add clear docstrings for the canonical meaning of:
  - local delivery
  - fanout
  - persistence
  - cross-service publish
  - command output writeback

### Acceptance

- New code paths no longer depend on raw `_noStateFanout` checks.
- Internal propagation decisions are driven by typed objects rather than magic dictionary keys.

## Phase 2: Unify Command Execution

### Objectives

- Collapse the current two command chains into one canonical execution engine.
- Keep protocol compatibility through adapters, not duplicate runtime logic.

### Checklist

- [x] Introduce a canonical internal `execute_command(...)` helper shared by command adapters as the first step toward a dedicated `CommandGateway`.
- [x] Introduce a `CommandGateway` internal component with one canonical method such as `invoke(node_id, call, args, options)`.
- [x] Route hidden command-state dispatch through the shared command execution core.
- [x] Route micro endpoint `_cmd` through the shared command execution core.
- [x] Decide and document one primary result policy:
  - reply only
  - hidden output state only
  - explicit opt-in dual result mode
- [x] Normalize argument mapping rules so hidden-state and micro command invocation behave identically.
- [x] Keep legacy hidden-state command support behind a compatibility adapter until downstream users are migrated.
- [x] Add deprecation notes for command behavior that will be removed.

### Acceptance

- There is only one runtime implementation of command execution.
- Hidden state commands and micro commands share argument normalization, execution, error handling, and output writeback policy.

## Phase 3: Simplify Data Delivery and Cross-Publish

### Objectives

- Separate local buffering semantics from cross-service publishing semantics.
- Remove accidental duplicate consumption models.

### Checklist

- [ ] Replace `publish_all_data: bool` with a more explicit policy:
- [x] Replace `publish_all_data: bool` with a more explicit policy:
  - `routed`
  - `all`
  - `none`
- [x] Change the default cross-publish policy from current eager over-publish behavior to routed-only publish.
- [x] Replace `DataDeliveryMode = "pull" | "push" | "both"` with clearer semantics, for example:
  - `buffered`
  - `callback`
- [x] Decide whether dual delivery should remain available; if yes, make it explicit and rare rather than default-facing.
- [x] Ensure pull-triggered local compute does not automatically become cross-service network output unless explicitly requested.
- [x] Add metrics counters for:
  - local-only emit
  - routed cross emit
  - suppressed cross publish
  - on_data callback delivery
  - buffer pull delivery

### Acceptance

- A service author can tell, from config alone, whether a sample is buffered locally, delivered by callback, published cross-service, or some explicit combination.
- Local pull evaluation no longer surprises callers by causing hidden network traffic.

## Phase 4: Harden Lifecycle and Registry Semantics

### Objectives

- Make runtime reuse and test behavior predictable.
- Remove hidden process-global state from default paths.

### Checklist

- [x] Stop clearing rungraph/service hook registrations inside `ServiceBus.stop()`, or re-register them explicitly on restart.
- [x] Define whether `ServiceRuntime` and `ServiceBus` are restartable objects; document and test that contract.
- [x] Stop defaulting `ServiceCliTemplate.build_registry()` to a process-global singleton.
- [x] Make shared registries opt-in rather than default.
- [x] Split "spec registration" and "runtime factory registration" APIs more clearly.
- [x] Change missing operator factory behavior from silent fallback to explicit structured errors.
- [x] Document and test that known services without a custom service runtime factory still fall back to generic `ServiceNode`.
- [ ] Continue migrating sibling package registry helpers from implicit singleton fallback to explicit fresh/shared registry APIs.
- [ ] Add strict tests for:
  - repeated describe
  - repeated CLI setup
  - repeated runtime creation
  - stop/start reuse

### Acceptance

- Repeated CLI or describe calls do not depend on singleton state.
- Missing factory registration fails loudly and locally.
- Restart semantics are explicit and covered by tests.

## Phase 5: Break Up the Large ServiceBus

### Objectives

- Convert the current shared mutable `ServiceBus` into a small orchestration facade over focused components.

### Target Internal Split

- `StateStore`
- `StateRouter`
- `DataRouter`
- `CommandGateway`
- `RungraphController`
- `LifecycleController`
- `MonitorReporter`

### Checklist

- [x] Extract state cache/persist/read logic from `ServiceBus`.
- [x] Extract state edge fanout and cross-state watch logic from `ServiceBus`.
- [x] Extract data buffer/subscription/push callback logic from `ServiceBus`.
- [x] Extract command binding and dispatch state from `ServiceBus`.
- [x] Reduce direct `bus._...` access across modules.
- [ ] Replace implicit shared mutable access with explicit constructor injection.
- [x] Keep `ServiceBus` as a thin façade for compatibility.

### Acceptance

- Most internal modules no longer reach through `bus._private_field`.
- `ServiceBus` becomes orchestration, not the storage location for all runtime state.

### Slice D status

- `DataRouter` is the canonical owner for data route tables, buffers, and data subscriptions.
- `StateStore` is the canonical owner for state cache, access maps, and KV-backed reads.
- `StateRouter` is the canonical owner for intra-state routes, cross-state bindings, remote watches, and ordering guards.
- `ServiceBus` now acts as the compatibility façade over those focused components.

## Phase 6: Public API Cleanup and Compatibility Pass

### Objectives

- Make the SDK surface match developer intuition.
- Clearly separate stable API from internal implementation details.

### Checklist

- [x] Define stable public modules, for example:
  - `f8pysdk.app`
  - `f8pysdk.specs`
  - `f8pysdk.registry`
  - `f8pysdk.nodes`
  - `f8pysdk.state`
  - `f8pysdk.data`
  - `f8pysdk.command`
  - `f8pysdk.transport`
  - `f8pysdk.testing`
- [ ] Remove wildcard exports from package root or replace them with explicit exports.
- [ ] Move compatibility re-export modules under a clearly marked compatibility boundary.
- [ ] Audit sibling packages for internal imports and migrate them to stable modules.
- [ ] Add deprecation warnings and migration notes for unstable paths such as deep `service_bus.*` imports.
- [ ] Update README examples to use the new canonical entrypoints only.

### Acceptance

- A new user can discover the SDK from a small number of obvious modules.
- Internal modules are no longer the default import path for sibling packages.

## Recommended Implementation Order

### Slice A

- Phase 0 tests
- lifecycle restart contract
- registry singleton cleanup

Why first:

- smallest semantic blast radius
- immediately improves testability and confidence

### Slice B

- command unification

Why second:

- removes the most obvious duplicated response chain
- simplifies later API cleanup

### Slice C

- data delivery and cross-publish simplification

Why third:

- larger compatibility impact
- easier after command/state semantics are clearer

### Slice D

- internal component extraction
- public API cleanup

Why last:

- best done after behavior is already simplified

## Suggested First Working Milestone

If we want the first code-change milestone to be low risk and high leverage, do this first:

- [x] Add Phase 0 regression tests.
- [x] Fix hook clearing on stop or document non-restartability and enforce it.
- [x] Make `ServiceCliTemplate` use a fresh registry by default.
- [x] Add an explicit opt-in path for shared registries in tests or plugin discovery code.

This milestone does not redesign the SDK yet, but it makes the system much safer to refactor.

## Tracking Notes

- 2026-04-06
  - owner: Codex + repository maintainer
  - scope: Slice A baseline hardening
  - completed:
    - documented single-run lifecycle for `ServiceBus` and `ServiceRuntime`
    - switched `ServiceCliTemplate`, `ServiceRuntime`, and `ServiceHost` defaults to fresh registries
    - added explicit shared-registry opt-in
    - added regression coverage for over-publish, dual delivery, registry contamination, missing operator fallback, and split command semantics
    - added a current-state architecture note for canonical runtime chains
  - compatibility notes:
    - restart now requires constructing a new `ServiceBus` / `ServiceRuntime`
    - process-global registry sharing remains available only through explicit opt-in

- 2026-04-06
  - owner: Codex + repository maintainer
  - scope: Slice B command-path unification
  - completed:
    - introduced a shared internal `execute_command(...)` entrypoint in `service_bus.command_runtime`
    - introduced a dedicated internal `CommandGateway` component and wired `ServiceBus` to it
    - introduced typed command invoke/output policy objects instead of open-coded command result metadata
    - routed hidden command-state dispatch and micro `_cmd` through the same execution core
    - unified node lookup, argument normalization, handler invocation, error classification, and hidden output writeback logic
    - added `ServiceBus.invoke_command(...)` as the explicit local SDK command façade with reply-first default behavior
    - aligned Studio UI command buttons to hidden command-state writes so service and operator button-triggered commands share graph semantics
    - changed micro `_cmd` to reply-first and removed its implicit hidden output writeback behavior
    - added regression coverage for missing-target handling, handler failure handling, and non-fatal writeback failure
  - compatibility notes:
    - hidden-state command input remains the graph-compatibility adapter and still accepts scalar/list/dict payloads
    - micro `_cmd` now accepts scalar/list/dict payloads for declared commands and normalizes them with the same rules as hidden-state input
    - undeclared commands without param metadata now require object-shaped args on request/reply surfaces
    - request/reply command paths are reply-first; hidden output writeback is now explicit opt-in on the local SDK façade only
    - hidden output writeback failures now log once and do not fail the command invocation itself

- 2026-04-06
  - owner: Codex + repository maintainer
  - scope: Slice C data delivery and cross-publish simplification
  - completed:
    - introduced explicit `cross_publish_policy = "routed" | "all" | "none"` and made `routed` the default
    - changed canonical local data delivery names to `callback | buffered | both`
    - kept `publish_all_data` and `data_delivery="push"|"pull"` as compatibility aliases
    - separated local delivery from cross-service publish decisions in the routing layer
    - stopped pull-triggered local compute from implicitly cross-publishing network samples
    - extended monitor snapshots with routing counters for local-only emits, routed cross emits, suppressed cross publishes, callback deliveries, and buffered pull deliveries
    - added regression coverage for routed-default suppression, callback-only local delivery, compatibility alias mapping, and local-only pull compute
  - compatibility notes:
    - `publish_all_data=True` now maps to `cross_publish_policy="all"`; `False` maps to `routed`
    - `data_delivery="push"` maps to `callback`; `data_delivery="pull"` maps to `buffered`
    - `data_delivery="both"` remains available as explicit compatibility mode, but it is no longer the default-facing recommendation

- 2026-04-07
  - owner: Codex + repository maintainer
  - scope: Slice D initial internal extraction and API cleanup
  - completed:
    - extracted data-side mutable routing state into the data-side runtime owner `DataRouter`
    - moved data route tables, input buffers, routed subscriptions, custom subscriptions, and push-callback queue ownership behind `DataRouter`
    - changed `ServiceBus`, rungraph apply, lifecycle stop, and monitor queue sampling to delegate to `DataRouter`
    - introduced stable top-level modules `f8pysdk.codec` and `f8pysdk.state`
    - expanded `f8pysdk.testing` with buffered-input helpers so repo tests no longer import deep `service_bus.routing_data`
    - migrated sibling packages off deep `f8pysdk.service_bus.codec`, `state_*`, and `routing_data` imports
    - hardened `pyengine` service-node attach behavior so its pull-first delivery default is applied explicitly at runtime
  - compatibility notes:
    - `f8pysdk.service_bus.routing_data` remains available as a thin compatibility layer
    - `ServiceBus` still exposes the same public emit/pull/subscribe API, but the data runtime now lives behind `DataRouter`
    - repo-internal callers should prefer `f8pysdk.codec`, `f8pysdk.state`, and `f8pysdk.testing` over deep `service_bus.*` imports

- 2026-04-07
  - owner: Codex + repository maintainer
  - scope: Slice 1 semantic cleanup follow-up
  - completed:
    - introduced typed internal `DataEmitOptions` and `CrossPublishPlan` for data-side propagation decisions
    - refactored `DataRouter` to route normal emits and pull-triggered local compute through the same explicit data-side semantics
    - documented canonical state/data command semantics on the public `ServiceBus` façade
  - compatibility notes:
    - public `ServiceBus.emit_data(...)` / `pull_data(...)` signatures remain unchanged
    - pull-triggered local recompute is still local-only, but that behavior is now represented by typed router options instead of open-coded branching

- 2026-04-07
  - owner: Codex + repository maintainer
  - scope: Slice 6 public API cleanup bootstrap
  - completed:
    - introduced stable top-level modules `f8pysdk.app`, `f8pysdk.command`, `f8pysdk.data`, `f8pysdk.nodes`, `f8pysdk.registry`, and `f8pysdk.transport`
    - migrated SDK-internal callers away from `service_bus.codec`, `service_bus.bus`, and direct `nats_transport` imports where stable wrappers now exist
    - migrated representative SDK tests to the public import surfaces and added explicit coverage for the new top-level modules
    - moved stable test helpers off `service_bus.routing_data` so benchmarks/tests can stay on `f8pysdk.testing`
    - migrated repo-wide callers off `runtime_node`, `runtime_node_registry`, `service_cli`, `service_runtime`, `service_host`, and `nats_transport` import paths onto the stable public modules
    - promoted monitor snapshot collection to stable top-level module `f8pysdk.monitoring`
    - moved non-public test/runtime helpers behind explicit `f8pysdk.service_bus.internal` boundaries owned by the ServiceBus subsystem instead of a top-level pseudo-internal package
  - compatibility notes:
    - `f8pysdk.service_bus` remains the public bus entrypoint for `ServiceBus` and `ServiceBusConfig`
    - the earlier deep `service_bus.*` compatibility layers from this phase have since been removed
    - repo-internal callers that still need non-public helpers should import from concrete owner modules under `f8pysdk.service_bus.*`

- 2026-04-07
  - owner: Codex + repository maintainer
  - scope: Slice D owner-path cleanup for remaining deep runtime helpers
  - completed:
    - moved `StateStore` and `StateRouter` ownership under `service_bus.state.store` and `service_bus.state.router`
    - moved state-side metadata and inbound timestamp helpers into `service_bus.state.helpers`
    - moved shared deduped logging and capped-cache helpers into `service_bus.internal.logging` and `service_bus.internal.cache`
    - moved lifecycle/rungraph metadata builders into `service_bus.workflow.metadata`
    - converted legacy `metadata`, `payload`, `runtime_collections`, `error_utils`, `state_store`, and `state_router` modules into explicit compatibility shims with deprecation warnings
  - compatibility notes:
    - repo-internal callers should prefer concrete owner modules such as `f8pysdk.service_bus.state.*`, `f8pysdk.service_bus.workflow.metadata`, and `f8pysdk.service_bus.internal.*`
    - the temporary helper-level compatibility shells introduced during this migration have since been removed

- 2026-04-07
  - owner: Codex + repository maintainer
  - scope: Slice D owner-path cleanup for data-side package symmetry
  - completed:
    - promoted `service_bus.data.*` as the canonical owner package for data-side runtime internals
    - moved the real implementations to `service_bus.data.emit`, `service_bus.data.flow`, and `service_bus.data.router`
    - migrated temporary compatibility paths off the old `routing/*` namespace and then removed those shims
    - repointed repo callers and tests at direct data owner modules
  - compatibility notes:
    - repo-internal callers should prefer `f8pysdk.service_bus.data.*` and `f8pysdk.service_bus.state.*` as the symmetric owner packages for runtime data and state concerns
    - the legacy data-side routing compatibility namespace has since been removed

- 2026-04-07
  - owner: Codex + repository maintainer
  - scope: Slice D owner-path cleanup for state pipeline symmetry
  - completed:
    - moved the real state publish pipeline implementation to `service_bus.state.pipeline`
    - repointed `ServiceBus`, workflow, command, and internal state helpers to the new owner path
    - used a temporary migration shim during rollout and then removed the old `domain` compatibility layer
    - reduced the state-side runtime to explicit owner modules instead of a mixed owner/compat layout
  - compatibility notes:
    - repo-internal callers should prefer `f8pysdk.service_bus.state.pipeline` for state validation/persistence/local-delivery logic
    - the legacy state-pipeline compatibility path has since been removed

- 2026-04-07
  - owner: Codex + repository maintainer
  - scope: Slice D state protocol-type owner cleanup
  - completed:
    - promoted top-level `f8pysdk.state` as the canonical public owner for state read/write protocol types
    - repointed state-side runtime code to direct state owner modules instead of root-level state facade paths
    - moved state publish controls to `service_bus.state.options`
    - repointed `f8pysdk.service_bus` public exports directly at the stable top-level state types instead of via a separate `service_bus.types` barrel
  - compatibility notes:
    - public imports via `f8pysdk.state` and `f8pysdk.service_bus` remain unchanged
    - the temporary deep root state facade modules and `service_bus.types` compatibility barrel introduced during migration have since been removed

- 2026-04-07
  - owner: Codex + repository maintainer
  - scope: Slice E command subsystem extraction
  - completed:
    - moved command input bindings, command output bindings, hidden command field tracking, and hidden dispatch state out of `ServiceBus` and into `CommandGateway`
    - changed state write local-delivery and rungraph apply paths to consult `bus.command_gateway` instead of reading command-specific `bus._private` fields
    - injected the node registry mapping into `CommandGateway` so command binding refresh no longer depends on whole-bus mutable state
    - removed the now-redundant command binding refresh helper shim
    - added regression coverage that unregistering a node also clears command hidden bindings
  - compatibility notes:
    - public `ServiceBus.invoke_command(...)` behavior remains unchanged
    - internal callers should prefer `bus.command_gateway` over command-specific `ServiceBus` private fields

- 2026-04-07
  - owner: Codex + repository maintainer
  - scope: Slice F registry API clarification and operator-factory strictness
  - completed:
    - introduced explicit `RuntimeNodeRegistry.register_service_factory(...)` and `register_operator_factory(...)` entrypoints alongside explicit `create_operator_node(...)` / `create_runtime_node(...)`
    - migrated repo-internal runtime registration helpers and tests off the ambiguous `register(...)` / `register_service(...)` names
    - changed missing operator runtime factories from silent generic-node fallback to structured `OperatorFactoryNotRegistered` errors
    - updated `ServiceHost` to skip operator nodes with missing runtime factories instead of materializing generic placeholder operators
    - exported the new registry error types from the stable `f8pysdk.registry` module and added regression coverage
  - compatibility notes:
    - existing `register(...)`, `register_service(...)`, and `create(...)` methods still delegate to the new explicit APIs for repo compatibility
    - generic `ServiceNode` remains the current default container when a service class is known but does not provide a custom service runtime factory

- 2026-04-07
  - owner: Codex + repository maintainer
  - scope: Slice G lifecycle hook retention cleanup
  - completed:
    - stopped clearing rungraph hooks and service hooks inside `ServiceBus.stop()`
    - added regression coverage that registered hooks remain attached after stop on single-run bus instances
  - compatibility notes:
    - `ServiceBus` and `ServiceRuntime` remain single-run objects; this change only removes surprising post-stop mutation of hook registration state

- When we start a phase, create a short changelog section here with:
  - date
  - owner
  - scope
  - migrated callers
  - compatibility notes
- Do not remove compatibility paths until downstream packages are migrated and covered.

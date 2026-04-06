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
- Missing runtime factories can silently degrade into generic nodes.
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

- [ ] Introduce a typed internal `StatePublishOptions` dataclass to replace magic propagation control keys like `_noStateFanout`.
- [x] Introduce a typed internal `CommandInvokeOptions` dataclass for command source/result policy metadata.
- [ ] Introduce a typed internal `DataEmitOptions` or `CrossPublishPolicy` abstraction.
- [ ] Move compatibility-only flags and hidden metadata shaping behind helper constructors instead of open-coded dict assembly.
- [ ] Add clear docstrings for the canonical meaning of:
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
  - `routed`
  - `all`
  - `none`
- [ ] Change the default cross-publish policy from current eager over-publish behavior to routed-only publish.
- [ ] Replace `DataDeliveryMode = "pull" | "push" | "both"` with clearer semantics, for example:
  - `buffered`
  - `callback`
- [ ] Decide whether dual delivery should remain available; if yes, make it explicit and rare rather than default-facing.
- [ ] Ensure pull-triggered local compute does not automatically become cross-service network output unless explicitly requested.
- [ ] Add metrics counters for:
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

- [ ] Stop clearing rungraph/service hook registrations inside `ServiceBus.stop()`, or re-register them explicitly on restart.
- [ ] Define whether `ServiceRuntime` and `ServiceBus` are restartable objects; document and test that contract.
- [ ] Stop defaulting `ServiceCliTemplate.build_registry()` to a process-global singleton.
- [ ] Make shared registries opt-in rather than default.
- [ ] Split "spec registration" and "runtime factory registration" APIs more clearly.
- [ ] Change missing operator/service factory behavior from silent fallback to explicit structured errors.
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

- [ ] Extract state cache/persist/read logic from `ServiceBus`.
- [ ] Extract state edge fanout and cross-state watch logic from `ServiceBus`.
- [ ] Extract data buffer/subscription/push callback logic from `ServiceBus`.
- [ ] Extract command binding and dispatch state from `ServiceBus`.
- [ ] Reduce direct `bus._...` access across modules.
- [ ] Replace implicit shared mutable access with explicit constructor injection.
- [ ] Keep `ServiceBus` as a thin façade for compatibility.

### Acceptance

- Most internal modules no longer reach through `bus._private_field`.
- `ServiceBus` becomes orchestration, not the storage location for all runtime state.

## Phase 6: Public API Cleanup and Compatibility Pass

### Objectives

- Make the SDK surface match developer intuition.
- Clearly separate stable API from internal implementation details.

### Checklist

- [ ] Define stable public modules, for example:
  - `f8pysdk.app`
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

- When we start a phase, create a short changelog section here with:
  - date
  - owner
  - scope
  - migrated callers
  - compatibility notes
- Do not remove compatibility paths until downstream packages are migrated and covered.

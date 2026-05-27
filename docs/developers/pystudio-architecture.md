# PyStudio Architecture Notes

PyStudio is intentionally split across UI, graph authoring, local runtime orchestration, remote service control, and monitoring. The system is large enough that new code should protect module boundaries explicitly instead of adding another method to a central Qt object.

## Current Boundaries

- `f8pystudio.bridge`: runtime orchestration, service process control, rungraph deployment, remote state/command transport, and service lifecycle state.
- `f8pystudio.nodegraph`: graph model, node items, graph editing actions, runtime graph compilation, and session layout serialization.
- `f8pystudio.ui`: Qt widgets, main-window composition, dialogs, editor controls, and user-facing notifications.
- `f8pystudio.monitoring`: monitor snapshots, alert rows, and service table projections.
- `f8pystudio.assets`: component/variant/project persistence, sync, and catalog UI.
- `f8pystudio.diagnostics`: process logging, uncaught exception hooks, Qt message logging, and exception formatting.

## Runtime Bridge Rules

The bridge layer should keep protocol-specific identity logic out of mixins. The identity and liveliness protocol for remote service instances lives in `bridge.service_liveliness`:

- Zenoh liveliness key parsing.
- `serviceId -> runtimeInstanceId` identity extraction.
- Synchronous service liveliness queries.
- Runtime instance display formatting for deployment diagnostics.

Controller mixins may call these helpers, but should not duplicate Zenoh key parsing or string formats. This keeps deploy/restart/ACK logic easier to reason about and makes stale-instance bugs testable without a Qt bridge object.

## Recommended Refactoring Direction

Prefer small explicit modules with typed data objects over wide mixins with implicit attributes:

- Extract pure protocol or projection code first. These modules are easiest to test and hardest to accidentally couple to Qt.
- Keep Qt signal emission at the edge. A function that can return a dataclass should not require a `QObject`.
- Keep process lifecycle actions separate from deploy evidence. Starting/stopping a process and proving rungraph apply success are different responsibilities.
- Add direct tests for new boundary modules. Do not rely only on integration tests through `PyStudioServiceBridge`.

## External Tooling

External packages can help keep the architecture from regressing, but they will not replace the refactor:

- `import-linter` can enforce boundaries such as "bridge protocol helpers must not import UI" and "nodegraph must depend on bridge only through protocols".
- `grimp` can generate import graphs for local analysis.
- `pytest-archon` can express architecture rules in pytest style.

These should be introduced with narrow contracts first. A broad repo-wide rule will be noisy until existing modules are separated further.

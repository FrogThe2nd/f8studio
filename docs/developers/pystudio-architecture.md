# PyStudio Architecture Notes

PyStudio is intentionally split across UI, graph authoring, local runtime orchestration, remote service control, and monitoring. The system is large enough that new code should protect module boundaries explicitly instead of adding another method to a central Qt object.

## Current Boundaries

- `f8pystudio.bridge`: runtime orchestration, service process control, rungraph deployment, remote state/command transport, and service lifecycle state.
- `f8pystudio.nodegraph`: graph model, node items, graph editing actions, runtime graph compilation, and session layout serialization.
- `f8pystudio.ui`: Qt widgets, main-window composition, dialogs, editor controls, and user-facing notifications.
- `f8pystudio.monitoring`: monitor snapshots, alert rows, and service table projections.
- `f8pystudio.assets`: component/variant/project persistence, sync, and catalog UI.
- `f8pystudio.diagnostics`: process logging, uncaught exception hooks, and exception formatting. Qt message logging is installed from `f8pystudio.ui.support.qt_message_logging` at the application entrypoint so the diagnostics core stays UI-independent.
- `f8pystudio.automation`: typed graph patch/snapshot contracts, loopback control client/server, GUI automation host, CLI entrypoint, and MCP sidecar integration. Domain types stay UI-free; adapters are the only modules that cross into Qt/nodegraph/bridge.

## Runtime Bridge Rules

The bridge layer should keep protocol-specific identity logic out of mixins. The identity and liveliness protocol for remote service instances lives in `bridge.service_liveliness`:

- Zenoh liveliness key parsing.
- `serviceId -> runtimeInstanceId` identity extraction.
- Synchronous service liveliness queries.
- Runtime instance display formatting for deployment diagnostics.

Controller mixins may call these helpers, but should not duplicate Zenoh key parsing or string formats. This keeps deploy/restart/ACK logic easier to reason about and makes stale-instance bugs testable without a Qt bridge object.

Rungraph apply evidence is similarly isolated in `bridge.rungraph_deploy_evidence`:

- Retained rungraph config key formatting.
- Retained rungraph fingerprint decoding.
- Request-scoped apply status tracking.
- Timeout/error message construction for ACK diagnostics.

`bridge.rungraph_deployer` owns transport, retained watchers, endpoint probing, and retry timing. It should delegate status-payload interpretation to the evidence helper instead of growing another inline state machine.

Service availability decisions should keep remote status interpretation in `bridge.service_availability`:

- Convert status endpoint payloads into explicit identity objects.
- Decide whether a live/local service can be reused.
- Distinguish unreachable status, old identity protocol, and service-class mismatch.

`bridge.service_lifecycle_controller` still owns process cleanup, launch, cache updates, and user-visible logs. It should ask the availability helper what the status means instead of duplicating protocol checks in each branch.

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

## GUI Automation Boundary

PyStudio automation is disabled by default. Launch with `python -m f8pystudio.main --automation` to start a loopback-only control server for CLI and MCP sidecars. The GUI process writes a token file and connection metadata under `~/.f8/studio/automation/` using private file permissions; sidecars must authenticate every request.

Automation clients must not mutate Qt objects directly. The local control server runs on a background thread and forwards each request to `StudioAutomationHost` on the Qt main thread. Graph changes are expressed as explicit typed patch operations (`createNode`, `connectPorts`, `setNodeState`, etc.) instead of JSONPath or dynamic attribute dispatch. Mutating requests include `expectedRevision` so LLM-driven edits can fail fast when the user has changed the graph.

Longer observation requests stay off the Qt thread. `runtime.watchState` waits on the automation observation store from the server thread, while `runtime.samplePort` installs a bounded short-lived subscription through the bridge runtime transport and returns capped JSON-safe samples. JSON data values may be included when they fit the caller's `maxValueBytes`; binary or oversized payloads return metadata only. High-frequency counters and port output samples remain monitor/data-channel concerns, not service `stateFields`.

Recommended Codex MCP setup uses a loopback streamable HTTP MCP gateway. In the PyStudio GUI, use `Tools -> MCP HTTP Server` to start or stop the gateway for the active instance. From the repository root, the same HTTP-only gateway can be started with:

```bash
pixi run f8pystudio_mcp
```

Then install the MCP endpoint into Codex:

```bash
codex mcp add f8pystudio --url http://127.0.0.1:8765/mcp
```

The GUI toggle starts the PyStudio automation host when needed and points the MCP gateway at that instance's connection file. The command-line gateway reads the default automation connection file from `~/.f8/studio/automation/connection.json`. When targeting a specific PyStudio instance from the command line, set `F8PYSTUDIO_CONNECTION_FILE` before starting the gateway, or pass `--connection-file` to the Python module:

```bash
F8PYSTUDIO_CONNECTION_FILE=~/.f8/studio/automation/connection.json pixi run f8pystudio_mcp
pixi run python -m f8pystudio.mcp.server --host 127.0.0.1 --port 8765 --path /mcp --connection-file ~/.f8/studio/automation/connection.json
```

Codex, Agent Framework clients, and PyStudio's GUI toggle should all use the same streamable HTTP endpoint at `http://127.0.0.1:8765/mcp`.

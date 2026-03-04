# Studio (GUI)

`f8pystudio` is the visual node-graph environment for building, debugging, and running service graphs.

## Launch

Recommended launch command:

```bash
pixi run -e default f8pystudio
```

Force live discovery (ignore static `describe.json` fast path):

```bash
python -m f8pystudio.main --discovery-live
```

## UI Overview

![Studio main window](../assets/studio/main-window.png)

The main window can be understood as 5 areas:

1. Top toolbar: session file actions (load/insert/save), send graph (`F5`), stop all services.
2. Center canvas: node graph editing and wiring.
3. Left Properties panel: selected node config (`State / Commands / Port / Node`).
4. Right Node Library: node search and placement.
5. Bottom Service Logs / Service Manager: logs and runtime status.

## Node Types

![Node kinds](../assets/studio/node-kinds.png)

Common node categories:

1. `Service Node`
2. `Operator Node`
3. `UI Node` (`f8.pystudio.*` visualization nodes)

Key constraints:

1. Operator nodes must be bound to a container service (usually `f8.pyengine`), so `Service Id` must point to a container node `id`.
2. UI nodes belong to local `f8.pystudio` service.
3. Cross-service communication goes through rungraph/NATS, not direct in-process calls.

## 5-Minute Quick Start

Use this minimal flow to get started:

1. Launch Studio and press `Tab` for quick node search.
2. Place nodes from Node Library: `IM Player`, `PyEngine`, `Python Script`, `TrackViz`.
3. Select `Python Script` and set `Service Id` in `State` tab to the `id` of your `PyEngine` node.
4. Click `Code -> Edit...` to open code editor and update script logic.
5. Wire nodes by port type (see edge rules) and configure required state fields.
6. Click Send Graph (paper plane icon, `F5`).
7. Start services from node toolbar or `Service Manager`, then watch `Running/Alive/Ready/Active` and logs.

## Edge Rules

Studio enforces 3 independent edge kinds:

1. `exec` (white)
2. `data` (gray)
3. `state` (yellow)

Rules:

1. Only same-kind ports can connect: `exec->exec`, `data->data`, `state->state`.
2. `exec` connections are only allowed between operators in the same `svcId`.
3. `exec` ports are single-in and single-out (reconnect replaces old edge).
4. `data` and `state` support cross-service links, but input ports are single-in.
5. On loading legacy sessions, invalid edges are stripped and logged as warnings.

Use `Pipe Visibility` (`EXEC / DATA / STATE`) to toggle each edge kind independently.

## Runtime Control and States

Each service node has a compact process toolbar (disable/start or activate/stop/sync/restart).

Status examples:

| Status | Screenshot | Meaning |
| --- | --- | --- |
| Not Run | ![not run](../assets/studio/status-not-run.png) | Process not started |
| Disabled | ![disabled](../assets/studio/status-disabled.png) | Node is excluded from rungraph and auto-start |
| Running | ![running](../assets/studio/status-running.png) | Process running |
| Paused | ![paused](../assets/studio/status-paused.png) | Process running but inactive |

`Service Manager` provides centralized control and monitoring, including CPU/RAM/GPU, latency, and error counters.

## Properties Panel

![State tab](../assets/studio/properties-state-tab.png)

`Properties` has four tabs:

1. `State`: state fields and editors
2. `Commands`: command declarations
3. `Port`: data input/output port definitions
4. `Node`: visual appearance

Related screenshots:

![Commands tab](../assets/studio/properties-commands-tab.png)
![Port tab](../assets/studio/properties-port-tab.png)
![Node tab](../assets/studio/properties-node-tab.png)

### Field / Command / Port Editing

![Edit state field](../assets/studio/edit-state-field.png)
![Edit command](../assets/studio/edit-command.png)
![Edit data port](../assets/studio/edit-data-port.png)

Editable metadata includes:

1. `name`, `description`, `required`, `showOnNode`
2. State field settings like `access`, `uiControl`, `uiLanguage`, `valueSchema`
3. Command parameters (`params`)
4. Data port `valueSchema`

## Schema Editor

![Schema UI](../assets/studio/schema-editor-ui.png)
![Schema JSON](../assets/studio/schema-editor-json.png)

Schema editor has both `UI` and `JSON` views. `valueSchema` is used for:

1. Runtime value validation
2. Property editor rendering
3. Python editor completion and hints context

## Code Editor (Monaco)

![Monaco editor](../assets/studio/code-editor-monaco.png)

The code editor is Monaco-based (same core as VS Code). Common shortcuts:

1. `Ctrl+S`: save
2. `Ctrl+Q`: close
3. `Ctrl+Space` or `Ctrl+J`: trigger completion
4. `Ctrl+Shift+Space` or `Ctrl+Shift+J`: trigger parameter hints
5. `Esc`: dismiss completion popup

## Node Library and Variants

![Node library](../assets/studio/node-library.png)

Node Library supports search by name/tags/description. Click a node, then left-click on canvas to place it. Right-click menu includes:

1. `Show Details`: view spec docs and raw JSON
2. `Manage Variants...`: open variant manager
3. `Delete Variant...`: remove current variant (variant item only)
4. `Variants`: place an existing variant directly

![Context menu](../assets/studio/node-library-context-menu.png)
![Show details](../assets/studio/node-library-show-details.png)
![Variant manager](../assets/studio/node-variant-manager.png)

Variant storage file:

`~/.f8/studio/nodeVariants.json`

## Common Shortcuts

1. `Tab`: open quick node search
2. `Delete` / `Backspace`: delete selected nodes
3. `Esc`: cancel node placement / graph insertion placement
4. `Ctrl+S`: save current session to `~/.f8/studio/lastSession.json`
5. `Ctrl+O`: load `lastSession.json`
6. `Ctrl+Shift+O`: load session from file
7. `Ctrl+Shift+S`: save session as
8. `Ctrl+Shift+I`: insert external graph into current canvas
9. `Ctrl+R`: compile and print runtime graph
10. `F5`: send graph to runtime

Canvas navigation:

1. Middle mouse drag: pan
2. `W/A/S/D` or arrow keys: pan
3. `Q/E` or `PageUp/PageDown`: zoom

## Session and Runner

Studio auto-saves the last session on exit to:

`~/.f8/studio/lastSession.json`

For headless execution without GUI:

```bash
python -m f8pysdk.headless_runner --session path/to/session.json
```

## Troubleshooting

1. Cannot connect ports: verify type match (`[E]/[D]/[S]`); `exec` cannot cross `svcId`.
2. Deploy/compile blocked: check missing dependency nodes or invalid/missing operator `Service Id`.
3. Command buttons disabled: node may be `Disabled`, or service process is not running.
4. Weak code completion: define clearer `valueSchema` on state/data ports.

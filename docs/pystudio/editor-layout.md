# Editor Layout

PyStudio is organized around one central idea: the canvas stores the graph shape, while the side panels expose the spec and runtime state of the currently selected node.

## Main Areas

![Main window](../assets/studio/main-window.png)

1. Top toolbar: session, deploy, and graph-wide actions
2. Center canvas: service nodes, operators, UI nodes, notes, and connections
3. Left `Properties` panel: state fields, commands, ports, and node visuals
4. Right `Node Library`: searchable catalog of services, operators, UI nodes, and variants
5. Bottom/log areas: runtime feedback, service status, and errors

## Mental Model

- Service nodes own runtime processes and appear in the compiled rungraph.
- Operator nodes usually run inside a host service such as `f8.pyengine`; they need a valid `Service Id`.
- UI nodes live in local `f8.pystudio` and exist to inspect or control graph state while you edit.
- The canvas layout is only the editor view; deployment compiles that layout into a runtime graph.

## What To Read Next

- [Node Categories and Wiring](node-categories.md)
- [Lifecycle and Monitoring](lifecycle-and-monitoring.md)


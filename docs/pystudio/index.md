# PyStudio Guide

`f8pystudio` is the visual editor for building, inspecting, deploying, and debugging Feel8 service graphs.

Use this section when you already know how to launch Studio and want a more complete operating manual than the quickstart.

## What This Guide Covers

- Editor layout and the mental model for service graphs
- Node categories, port types, and safe wiring rules
- Service lifecycle, deploy flow, and runtime monitoring
- State/schema editing, command authoring, and Monaco-based code editing
- Node Library search, variants, session files, and graph reuse
- Troubleshooting, headless execution, and common failure patterns

## Recommended Reading Order

1. [Editor Layout](editor-layout.md)
2. [Node Categories and Wiring](node-categories.md)
3. [Lifecycle and Monitoring](lifecycle-and-monitoring.md)
4. [Schema and Code Editors](schema-and-code-editors.md)
5. [Node Library and Sessions](node-library-and-sessions.md)
6. [Debugging and Runner](debugging-and-runner.md)

## Launch Recap

```bash
pixi run -e default f8pystudio
```

For live discovery during development:

```bash
python -m f8pystudio.main --discovery-live
```

## Canonical Companions

- [Studio Quickstart](../getting-started/studio.md)
- [Modules Overview](../modules/index.md)
- [Node Atlas](../node-atlas/index.md)
- [Scenarios](../scenarios/index.md)


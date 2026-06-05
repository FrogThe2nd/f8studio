# PyStudio Agent Framework Rearchitecture

Status: implemented migration baseline, June 2026.

## Goal

PyStudio AI assistance is now organized around a typed `f8pystudio.agents` package that uses Microsoft Agent Framework (MAF) as the agent orchestration/provider layer while keeping Studio graph, editor, runtime, and MCP operations behind explicit PyStudio APIs.

The migration goals are:

- Remove Studio-owned direct chat-provider HTTP payload/client logic.
- Keep provider configuration, model selection, prompt assembly, sessions, and runtime calls behind one agent package.
- Reuse one typed Studio automation tool implementation for in-app agents and MCP.
- Let graph-level and code-editor-level assistants share sessions, context, and future memory scopes.
- Validate graph mutation through typed `GraphPatch` workflows instead of dynamic UI/object mutation.

## Implemented Package Layout

```text
f8pystudio/agents/
  __init__.py
  ag_ui.py
  clients.py
  codeact.py
  connectivity.py
  conversations.py
  graph_builder/
    __init__.py
    library_matcher.py
    pipeline.py
    plan_codec.py
    schema.py
  graph_context.py
  memory.py
  model_catalog.py
  prompts.py
  provider_endpoints.py
  provider_http.py
  qt_bridge.py
  registry.py
  runtime.py
  sessions.py
  state_store.py
  store.py
  tool_events.py
  tools/
    __init__.py
    graph.py
    mcp.py
    studio.py
```

The former `f8pystudio.ai_assist` implementation has been removed. Existing UI names such as `ai_assist_sidebar.py` remain as UI concepts, but their assistant runtime imports now point at `f8pystudio.agents`.

## Runtime Boundary

`f8pystudio.agents.runtime.StudioAgentRuntime` is the Qt-safe facade over MAF:

- `StudioAgentRequest` describes chat, edit, plan, and inline requests with explicit dataclass fields.
- `StudioAgentEvent` reports stream chunks, completion, and errors.
- `StudioAgentRuntime.run_text()` and `run_stream()` build MAF `Agent` instances and run them with optional tools and sessions.
- `StudioAgentSessionRegistry` maps Studio session keys to MAF sessions so sidebar, graph, editor, and node-scoped work can share continuity.

Qt and QWebChannel code uses `f8pystudio.agents.qt_bridge.AiLlmBridge`. That bridge preserves the frontend signal/slot API while delegating provider execution to `StudioAgentRuntime`.

## Agent UI Surface

PyStudio owns the production agent UI rather than embedding the MAF DevUI. MAF DevUI remains useful as a development and tracing surface, but it is not treated as a production Studio sidebar replacement.

The product UI now has a shared Qt agent control layer in `f8pystudio.ui.agents`:

- `AgentContextUsageButton` renders context budget state consistently for graph and editor surfaces.
- `AgentQuickSettingsController` owns the reusable model/provider quick settings toggle and panel.
- `AgentSurfaceScope` makes graph/editor/node scope explicit at the UI boundary.

Existing graph sidebar and Monaco editor surfaces reuse these controls instead of each implementing their own AI toolbar styling and context usage logic.

## Provider Layer

Provider configuration lives in explicit dataclasses:

- `ProviderConfig`
- `ModelInfo`
- `ModelCapabilities`

`f8pystudio.agents.clients` maps those configs to MAF provider clients:

- OpenAI-compatible, custom, and Ollama-style providers use MAF OpenAI-compatible clients.
- Anthropic requires the MAF Anthropic connector. Studio does not keep a handwritten Anthropic HTTP fallback.

`AiProviderStore` is now storage and model-cache management only:

- It persists provider configs and selected models.
- It can load bundled default model IDs for known providers.
- It supports manual model ID add/remove for custom/Ollama providers.
- It tests model connectivity by routing through `StudioAgentRuntime`, not by constructing provider HTTP payloads.

Automatic model discovery is intentionally not reimplemented in Studio unless MAF exposes a stable typed model-list API for that connector. This keeps provider protocol behavior in MAF rather than duplicating it.

## Prompt And Context Layer

Prompt construction moved into `f8pystudio.agents.prompts`.

Graph context moved into `f8pystudio.agents.graph_context` and remains typed around `GraphContextSnapshot`. Editor context still comes from `EditorAssistContext` and script/editor support payloads, then flows through `StudioAgentRequest`.

This keeps code-editor and graph-level assistance on the same runtime path while preserving the existing Monaco/editor frontend contract.

## Tool And MCP Layer

`f8pystudio.agents.tools.studio.StudioAutomationTools` is the canonical typed tool boundary for Studio graph/runtime operations.

The MCP server is now a thin registration layer in `f8pystudio.mcp.server` that forwards to the same tool implementation. This avoids maintaining one graph-operation implementation for in-app agents and another for MCP.

MAF MCP consumption is exposed through `f8pystudio.agents.tools.mcp`:

- `StudioMCPStdioConfig`
- `build_studio_mcp_stdio_tool()`

PyStudio owns domain semantics and mutation safety. MAF/MCP own tool transport, external tool consumption, and provider orchestration.

## AG-UI Direction

`f8pystudio.agents.ag_ui` provides a typed, dependency-light adapter spike for AG-UI-style event payloads:

- `AgUiRunEnvelope`
- `AgUiEvent`
- `encode_ag_ui_events()`
- `graph_patch_preview_event()`
- `runtime_evidence_event()`

The adapter maps `StudioAgentEvent` stream chunks/errors/completion into AG-UI-style event dictionaries and exposes custom Studio events for graph patch previews and runtime evidence. This is not a hard UI dependency. It is a future integration boundary for web agent clients, MAF DevUI experiments, or approval/event streaming prototypes.

## Graph Workflow Validation

`f8pystudio.agents.workflows.pyengine_sine_graph` provides a typed validation workflow for a small pyengine graph:

- Creates a `svc.f8.pyengine` service node.
- Creates `f8.pyengine.f8.phase`, `f8.pyengine.f8.cosine`, and `f8.pyengine.f8.range_map` operator nodes inside that service.
- Sets `phase.hz = 1.0`.
- Uses cosine with `phaseOffset = 0.75` to produce a sine-shaped signal.
- Maps input `[-1, 1]` to output `[0, 100]`.
- Connects `phase.phase -> sine.phase -> range_map.value`.

The test suite verifies both graph patch/catalog compatibility and runtime sample behavior:

```bash
QT_QPA_PLATFORM=offscreen pixi run pytest packages/f8pystudio/tests/test_agent_pyengine_sine_workflow.py -q
```

Expected result:

- patch preview/apply is valid
- output values stay in `[0, 100]`
- samples change over time, demonstrating a live 1 Hz signal path

## Studio Automation From Codex

With Studio launched in automation mode, an external agent such as Codex can operate PyStudio through the typed automation API:

1. Load the connection metadata from the automation connection file.
2. Authenticate with the generated token file.
3. Call `studio.status`, `graph.catalog`, `graph.previewPatch`, `graph.applyPatch`, `graph.compile`, runtime deploy/status/monitor methods, and related operations.
4. Use `StudioGraphAutomationAdapter` to resolve catalog/raw port names safely.

This means Codex can run Studio, apply a typed graph patch, compile it, and debug graph/runtime evidence through the API. Runtime live sampling depends on the running service backend and transport support; in-process pyengine tests validate the sine graph behavior independently of the GUI transport.

## Memory Direction

The implemented baseline includes typed session and memory scaffolding. The intended memory scopes are:

- `global`: stable user/provider preferences
- `project`: project-level decisions and conventions
- `graph`: graph design notes and debugging outcomes
- `node`: script-node purpose, edit history, schema expectations, and recent errors
- `editor_session`: Monaco conversation and document context
- `debug_session`: bounded runtime observations and hypotheses

High-frequency telemetry must stay on monitor/data channels. FPS, latency, frame counters, and per-frame output counts should not be service `stateFields` or long-term memory by default. Debugging workflows may summarize sampled evidence explicitly.

## Safety Rules

Graph and runtime mutation should continue to use typed operations:

- `graph.previewPatch`
- `graph.applyPatch`
- `graph.compile`
- `runtime.deploy`
- `runtime.serviceStatus`
- `runtime.readState`
- `runtime.writeState`
- `runtime.readMonitor`
- `runtime.samplePort`
- `runtime.invokeCommand`

Mutating workflows should preview before applying and preserve expected-revision checks. Agent tools should not mutate Qt objects directly.

## Validation

Core validation commands:

```bash
QT_QPA_PLATFORM=offscreen pixi run pytest packages/f8pystudio/tests/test_ai_provider_store.py packages/f8pystudio/tests/test_ai_provider_config_dialog.py packages/f8pystudio/tests/test_agent_runtime.py packages/f8pystudio/tests/test_agent_pyengine_sine_workflow.py packages/f8pystudio/tests/test_agent_tools.py -q
```

```bash
QT_QPA_PLATFORM=offscreen pixi run pytest packages/f8pystudio/tests -q
```

Migration audit scans:

```bash
rg -n "f8pystudio\\.ai_assist|AiHttpClient|_chat_payload_openai|_responses_payload_openai" packages/f8pystudio
```

```bash
rg -n "QtNetwork|QNetworkAccessManager|QNetworkRequest|_test_chat_url|_build_ping_payload|_models_url|models_path|chat_path" packages/f8pystudio
```

## Current Limits

- MAF connector availability still controls which providers are usable. Missing connector packages should fail clearly.
- Model listing is not duplicated in Studio. Unknown/custom providers require manual model IDs unless MAF gains a stable model discovery surface.
- Tool-call approval, richer workflow event UI, and durable project/node memory are future increments on top of the new package boundary.
- AG-UI support is currently an adapter spike, not a full HTTP/SSE endpoint.
- Agents must remain outside high-frequency script hooks such as per-frame `onData` or `onTick`.

## Reference Points

- Microsoft Agent Framework GitHub: https://github.com/microsoft/agent-framework
- Microsoft Agent Framework overview: https://learn.microsoft.com/en-us/agent-framework/overview/
- MAF MCP tools: https://learn.microsoft.com/en-us/agent-framework/agents/tools/local-mcp-tools
- MAF workflows overview: https://learn.microsoft.com/en-us/agent-framework/user-guide/workflows/overview
- MAF conversations and memory: https://learn.microsoft.com/en-us/agent-framework/agents/conversations/
- MAF memory and persistence: https://learn.microsoft.com/en-us/agent-framework/get-started/memory
- MAF providers overview: https://learn.microsoft.com/en-us/agent-framework/agents/providers/

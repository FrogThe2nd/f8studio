# Comm Bus Deep Dive: Zenoh Runtime, Data Streams, and State Sync

> Current status: the runtime is Zenoh-first. NATS Core, JetStream KV, and NATS Micro remain as explicit fallback implementations for `--bus-backend nats`; older NATS-specific terminology below should be read as the fallback mapping, not the default runtime path.

This document explains the **Comm Bus** design shared across:

- `f8pysdk` (Python runtime SDK)
- `f8cppsdk` (C++ runtime SDK)
- `f8pystudio` (PyStudio / the editor UI)

It focuses on **synchronization and propagation**:

- How we combine a high-speed **message fan-out** (Data Plane) with a reliable, observable, editable **state system** (State Plane)
- What constraints guarantee predictable behavior (initialization order, no cycles, single-upstream, dedupe)
- Whether this is a CRDT system (what is similar, what is not)

**Key entry points (recommended reading order):**

- Python: `packages/f8pysdk/f8pysdk/service_bus/api/bus.py`
- C++: `packages/f8cppsdk/src/service_bus.cpp`
- Studio: `packages/f8pystudio/f8pystudio/remote_state_watcher.py`, `packages/f8pystudio/f8pystudio/bridge/service_endpoint_client.py`

---

## Two Design Highlights

### 1) Three planes: Data / State / Control

We split “communication” into three planes, each optimized for a different contract:

- **Data Plane (high-throughput stream):** Zenoh pub/sub and latest-frame/latest-chunk transports
  Goal: throughput + low latency + fan-out.
  Contract: samples may be dropped or skipped; latest-frame consumers can skip stale frames rather than building backlog.
- **State Plane (reliable, inspectable state):** service-owned latest state with Zenoh queryables and update publishes
  Goal: “current value”, watchable, readable on demand, editable from Studio.
  Contract: “register-like” state keys with a strict write pipeline and topology constraints. NATS fallback maps this to JetStream KV.
- **Control Plane (request/reply, rejectable):** Zenoh queryables
  Goal: deploy/control operations must validate and **return a decision**.
  Examples: `set_rungraph`, `set_state`, `activate/deactivate/status/terminate`, `cmd`. NATS fallback maps this to NATS Micro endpoints.

### 2) Topology constraints instead of general conflict merging

State propagation stays simple and explainable because the rungraph enforces strong constraints for **state edges**:

- **No cycles** (prevents feedback oscillation)  
  See: `packages/f8pysdk/f8pysdk/rungraph_validation.py::validate_state_edges_or_raise(... forbid_cycles=True)`
- **No multi-upstream per state field** (one upstream source per target field)  
  See: `... forbid_multi_upstream=True`

This avoids the classic CRDT hard part (multi-writer conflict merge). Instead, a state edge is a **directional binding**: downstream follows upstream.

---

## Architecture Overview (Three-Plane Loop Under One Rungraph)

``` mermaid
---
config:
  layout: dagre
  look: neo
  theme: mc
---
flowchart TB
  Studio["PyStudio (UI)"]:::studio
  subgraph NATS["NATS (Core Pub/Sub + JetStream KV + Micro)"]
    Core["Core Pub/Sub<br/>(high-throughput)"]:::nats
    KV["JetStream KV<br/>(per-service bucket)"]:::nats
    Micro["Micro Endpoints<br/>(request/reply)"]:::nats
  end

  subgraph SvcA["Service A (pysdk/cppsdk runtime)"]
    BusA["ServiceBus"]:::svc
    NodesA["Runtime Nodes"]:::svc
  end
  subgraph SvcB["Service B (pysdk/cppsdk runtime)"]
    BusB["ServiceBus"]:::svc
    NodesB["Runtime Nodes"]:::svc
  end

  %% Control plane
  Studio -->|"deploy/control<br/>svc.{serviceId}.{endpoint}"| Micro
  Micro -->|"set_rungraph / set_state / status / ..."| BusA

  %% State plane
  BusA <-->|"KV put/get/watch<br/>bucket=svc_{serviceId}"| KV
  BusB -->|"watch peer bucket<br/>(cross-service state binding)"| KV
  Studio -->|"watch buckets<br/>(state/properties panels)"| KV

  %% Data plane
  BusA -->|"publish<br/>svc.{serviceId}.nodes.{nodeId}.data.{portId}"| Core
  Core -->|"fan-out deliver"| BusB

  BusA <--> NodesA
  BusB <--> NodesB

  classDef nats fill:#E3F2FD,stroke:#64B5F6,color:#0D47A1;
  classDef studio fill:#FFEBEE,stroke:#EF9A9A,color:#B71C1C;
  classDef svc fill:#E8F5E9,stroke:#81C784,color:#1B5E20;
```

---

## Naming & Resource Hierarchy (Subject / Bucket / Key)

### NATS subjects

``` mermaid
flowchart LR
  S1["svc.{serviceId}.nodes.{nodeId}.data.{portId}"] -->|"Data Plane<br/>fan-out subject"| D[Data]
  S2["svc.{serviceId}.{endpoint}"] -->|"Control Plane<br/>micro endpoint"| E[Endpoint]
  S3["svc.{serviceId}.cmd"] -->|"Control Plane<br/>command channel"| C[Cmd]
```

Cross-language implementations (must stay consistent):

- Python: `packages/f8pysdk/f8pysdk/nats_naming.py`
- C++: `packages/f8cppsdk/src/f8_naming.cpp`

### JetStream KV: one bucket per service

``` mermaid
flowchart TB
  B["Bucket: svc_{serviceId}"] --> K1["rungraph"]
  B --> K2["ready"]
  B --> K3["nodes.{nodeId}.state.{field}"]
```

Key semantics:

- `rungraph`: “last successfully applied” rungraph snapshot for the service
- `ready`: service readiness marker (used by Studio/launcher for timing and health)
- `nodes.{nodeId}.state.{field}`: node state/parameters (watchable + editable)

---

## Data Plane (High-Throughput Message Fan-Out)

### Contract and semantics

Data edges represent **streams**, not durable state:

- Producer calls `emit_data(...)` once → **one publish** on a single subject → many subscribers (fan-out)
- Consumers do not receive callbacks by default; they read from **input buffers** with a strategy:
  - `latest`: return newest and clear the queue
  - `queue`: FIFO pop (frame-by-frame processing)
- Optional `timeoutMs`: if the newest sample is stale, treat as “not available”

Implementation references:

- Python: `packages/f8pysdk/f8pysdk/service_bus/routing/data_flow.py`  
  (`emit_data`, `on_cross_data_msg`, `pull_data`, buffering + strategies)
- C++: `packages/f8cppsdk/src/data_bus.cpp`, `packages/f8cppsdk/src/rungraph_routes.cpp`

### Why callback is the default

`f8pysdk` defaults `ServiceBusConfig.data_delivery = "callback"` because:

- Callback delivery invokes `on_data(...)` for active consumers while still maintaining the current local input buffer.
- Components that only need pull semantics use `"buffered"` to skip callbacks and read current values through `pull_data(...)`.
- Legacy `"push"` / `"pull"` / `"both"` aliases are rejected so services do not silently run with the wrong delivery semantics.

### Cross-service data fan-out (sequence)

``` mermaid
sequenceDiagram
  participant P as Producer Node
  participant BusA as ServiceBus(A)
  participant N as NATS Core Pub/Sub
  participant BusB as ServiceBus(B)
  participant C as Consumer Node

  P->>BusA: emit_data(node, out_port, value)
  BusA->>BusA: buffer intra edges (in-process)
  BusA->>N: publish svc.A.nodes.X.data.port {value, ts}
  N-->>BusB: deliver (fan-out)
  BusB->>BusB: push_input -> buffer_input(to_node, to_port)
  C->>BusB: pull_data(to_node, in_port)<br/>(latest/queue + timeoutMs)
  BusB-->>C: value / None
```

---

## State Plane (Reliable, Observable, Editable State)

### State is not a message; it is a KV “current value”

Each service owns a JetStream KV bucket (`svc_{serviceId}`). For any state field, you can always:

- `kv_get`: fetch current value (initial sync, reconnect, UI open)
- `kv_watch`: subscribe to updates (real-time UI + cross-service bindings)

### State write pipeline (Python reference implementation)

The `publish_state(...)` pipeline (see `packages/f8pysdk/f8pysdk/service_bus/domain/state_pipeline.py`) is the canonical contract:

1. **Access control** via `F8StateAccess` (`ro/rw/wo`) and `StateWriteOrigin` (`runtime/rungraph/external/system`)
2. **Node validation hook** via `node.validate_state(...)` (accept/transform/reject with `StateWriteError`)
3. **Value normalization** (`coerce_state_value(...)`) to keep KV JSON-friendly (and stable across languages)
4. **Value dedupe** (identical values are not re-published)
5. **Persist** to KV: `kv_put(nodes.{nodeId}.state.{field})`
6. **Immediate local apply** (critical UX): do not wait for a watch round-trip; apply locally right away:
   - `node.on_state(...)`
   - intra-service state fanout (state edges)

### State payload shape (MsgPack map)

Both Python and C++ write MsgPack maps with a compatible shape (fields may vary by writer):

- `value`: the state value
- `ts`: best-effort timestamp in ms
- `actor`: writer identity (usually `serviceId`)
- `origin`: `runtime/rungraph/external/system`
- `source`: more specific tag (e.g. `state_edge_cross`, `endpoint`, `runtime`)
- extra meta: preserved, excluding reserved keys

### UI edit loop (Studio → Service → KV → Studio)

``` mermaid
sequenceDiagram
  participant UI as PyStudio UI
  participant N as NATS Micro (request/reply)
  participant Bus as ServiceBus (runtime)
  participant KV as JetStream KV
  participant Node as Runtime Node(s)
  participant Watch as Studio RemoteStateWatcher

  UI->>N: request svc.{serviceId}.set_state<br/>{nodeId, field, value}
  N-->>Bus: micro endpoint handler
  Bus->>Bus: validate / access / dedupe / coerce
  Bus->>KV: kv_put bucket=svc_{serviceId}<br/>key=nodes.{nodeId}.state.{field}
  Bus->>Node: node.on_state(field, value)<br/>(immediate local apply)
  Bus->>Bus: intra-state fanout (state edges)
  KV-->>Watch: watch update
  Watch-->>UI: update state + properties panel
```

Studio-side implementation:

- “Observe”: `packages/f8pystudio/f8pystudio/remote_state_watcher.py` (watch KV directly; no monitor node required)
- “Edit”: `packages/f8pystudio/f8pystudio/bridge/service_endpoint_client.py::request_set_remote_state(...)`

---

## Cross-State (Remote KV Binding → Local Field)

Cross-service state edges are **bindings**, not pub/sub messages:

- Downstream watches the upstream service bucket/key
- For every upstream update, downstream writes the value into its local target field
- Local write then triggers local node updates + intra-state fanout

Python references:

- Build binding tables: `packages/f8pysdk/f8pysdk/service_bus/workflow/cross_state.py::update_cross_state_bindings(...)`
- Start watches + initial sync: `...::sync_cross_state_watches(...)`
- Apply remote update: `...::on_remote_state_kv(...)`

### Strong initialization (why it matters)

After a rungraph deploy, initial cross-state values often define key parameters. If initialization is not staged, you can get UI/node “thrash” (A→B→C) and order-dependent outcomes.

The Python ServiceBus uses a **two-phase init**:

1. **Phase 1 (materialize remote values without fanout):** initial `kv_get` applies remote values with `no_fanout=True`
2. **Phase 2 (one ordered intra-state init fanout):** run a single `initial_sync_intra_state_edges(...)` traversal from roots to avoid linear edge-scan order dependence

### Cross-state init + watch (sequence)

``` mermaid
sequenceDiagram
  participant BusDown as Downstream ServiceBus
  participant KVUp as Upstream KV Bucket (svc_{peerServiceId})
  participant KVDown as Downstream KV Bucket (svc_{serviceId})

  Note over BusDown: After rungraph apply
  BusDown->>KVUp: watch nodes.{remoteNodeId}.state.{field}
  BusDown->>KVUp: kv_get initial value (concurrency-limited)
  KVUp-->>BusDown: current payload
  BusDown->>KVDown: kv_put local target (no fanout)
  Note over BusDown: Then run a single ordered intra-state init sync
  KVUp-->>BusDown: watch update (later)
  BusDown->>KVDown: kv_put local target (fanout enabled)
```

---

## Rungraph Apply = The Synchronization Contract

A key invariant: **the `rungraph` KV snapshot represents a successfully applied graph** (not “just requested”).

Studio deploys via the `set_rungraph` endpoint and receives an explicit accept/reject response (see `packages/f8pystudio/f8pystudio/deploy.py::deploy_to_service(...)`).

### Deploy/apply sequence (simplified)

``` mermaid
sequenceDiagram
  participant Studio as PyStudio
  participant N as NATS Micro
  participant Bus as ServiceBus
  participant KV as JetStream KV (svc_{serviceId})

  Studio->>N: request svc.{serviceId}.set_rungraph(graph)
  N-->>Bus: handler
  Bus->>Bus: validate (no cycles, single-upstream, writable targets)
  Bus->>Bus: rebuild routes (data + state)
  Bus->>Bus: seed stateValues (reconcile by meta.ts, no fanout)
  Bus->>Bus: start cross-state watches + initial sync (no fanout)
  Bus->>Bus: initial_sync_intra_state_edges (ordered)
  Bus->>KV: kv_put rungraph (only if apply ok)
  Bus-->>Studio: ok / error
```

---

## Is This a CRDT System?

If by CRDT you mean:

- multi-writer concurrent updates,
- no centralized ordering service,
- offline/partition-tolerant op-based merge that converges after gossip,

then **no**: our state system is not a general CRDT framework.

Why:

1. We rely on **Zenoh queryables + update publishes** as the default transport and per-service current-state substrate.
2. Updates are **whole-value writes** to service-owned state keys, not a CRDT operation set with a merge function.
3. We avoid conflicts primarily through **rungraph constraints** (no cycles, single-upstream), rather than general merge.

What is “CRDT-like”:

- Each state field behaves similarly to an **LWW register** in the sense that observers converge to the service-owned “current value”.
- Cross-state binding adds **out-of-order guards** to improve stability for downstream consumers.

More accurate description: a distributed state replication system with **service-owned latest state as the source of truth**, plus rungraph-enforced topology constraints and watch-based synchronization. The NATS fallback maps the same API to JetStream KV.

---

## Design Points & Hard Problems (and How We Address Them)

### 1) Initialization order (avoid intermediate-state thrash)

Problem: a target field might be influenced by:

- rungraph `stateValues`,
- cross-state initial values,
- local runtime bootstrap writes (`active`, identity fields, etc.)

Solution: stage initialization (no-fanout materialization first, ordered fanout later):

- `apply_rungraph_state_values(...)` seeds with `_noStateFanout=True`
- `sync_cross_state_watches(...)` initial reads apply with `no_fanout=True`
- `initial_sync_intra_state_edges(...)` runs once in an ordered traversal

### 2) Prevent feedback loops and redundant writes

Solution stack:

- Rungraph validation forbids state-edge cycles and multi-upstream.
- The state pipeline includes value dedupe to avoid “same value” republish loops.

### 3) UI edits vs runtime writes (who wins?)

Solution stack:

- Write access gate: `F8StateAccess` + `StateWriteOrigin`
- Rungraph reconcile semantics: `stateValues` do not clobber newer KV state (based on rungraph meta timestamp)

### 4) Backpressure for high-rate producers

Solution stack:

- Pull-based consumption + buffering decouple receiving from processing.
- Push-mode (when enabled) coalesces per-port updates and batches delivery.

### 5) “Peer service is not ready yet”

Solution:

- KV watchers handle missing remote buckets/streams by retrying in the background rather than failing permanently.
  See: `packages/f8pysdk/f8pysdk/nats_transport.py::kv_watch_in_bucket(...)`

### 6) Exceptions in high-frequency paths

Solution:

- Treat watchers and fanout loops as safety boundaries.
- Log with dedupe (`log_error_once`, `_rungraph_apply_error_once`, `_error_once`) to avoid log spam and performance collapse.

---

## What Preserves Interaction UX (fast local feedback + eventual convergence)

From a UI perspective, we want “edit feels instant” and “everyone eventually agrees”.

We achieve this by combining:

1. **Rejectable writes** (Control Plane): Studio uses `set_state` micro endpoint and gets ok/error
2. **Immediate local apply** (State pipeline): the runtime applies local writes to `node.on_state(...)` right away (no wait for watch)
3. **KV as source of truth** (State Plane): Studio watches KV and converges to the final current values
4. **Data plane isolation**: high-rate data streams do not directly invoke slow callbacks by default

---

## When to Use Data vs State

- Use **Data (pub/sub)** when you care about a stream of samples and can tolerate drops; throughput/latency are the priority.
  - Examples: feature streams, trackers, visualizations, monitoring samples
- Use **State (KV)** when you care about the current value and need read-after-open, watchability, and Studio editability.
  - Examples: parameters, mode toggles, rungraph snapshots, `ready/active`

---

## Implementation Layers (Python ServiceBus)

The Python implementation follows a layered architecture (see `packages/f8pysdk/f8pysdk/service_bus/ARCHITECTURE.md`):

``` mermaid
flowchart TB
  API["api/ (ServiceBus facade)"]
  WF["workflow/ (rungraph + cross-state + lifecycle)"]
  DOM["domain/ (state write policy/pipeline)"]
  ROUTE["routing/ (data flow + buffers + subscriptions)"]
  AD["adapters/ (infra integration, e.g. NATS micro)"]

  API --> WF
  API --> DOM
  API --> ROUTE
  API --> AD

  WF --> DOM
  WF --> ROUTE
  WF --> AD

  DOM --> AD
  ROUTE --> AD
```

---

## Code Map (by responsibility)

### Python (`f8pysdk`)

- ServiceBus facade + shared state/data APIs: `packages/f8pysdk/f8pysdk/service_bus/api/bus.py`
- Data plane (buffering, pull/push delivery): `packages/f8pysdk/f8pysdk/service_bus/routing/data_flow.py`
- State write pipeline (validate/dedupe/persist/local apply): `packages/f8pysdk/f8pysdk/service_bus/domain/state_pipeline.py`
- Cross-state (remote KV watch + initial sync): `packages/f8pysdk/f8pysdk/service_bus/workflow/cross_state.py`
- Rungraph apply (validate + rebuild + init sync): `packages/f8pysdk/f8pysdk/service_bus/workflow/rungraph.py`
- Transport (NATS Core + JetStream KV): `packages/f8pysdk/f8pysdk/nats_transport.py`
- Naming (subjects/keys/buckets): `packages/f8pysdk/f8pysdk/nats_naming.py`
- Micro endpoints (control plane): `packages/f8pysdk/f8pysdk/service_bus/adapters/micro.py`

### C++ (`f8cppsdk`)

- Naming (must match Python): `packages/f8cppsdk/src/f8_naming.cpp`
- State writes + ready flag: `packages/f8cppsdk/src/state_kv.cpp`
- Data publish: `packages/f8cppsdk/src/data_bus.cpp`
- Control plane (micro endpoints server): `packages/f8cppsdk/src/service_control_plane.cpp`
- Rungraph + cross-state logic: `packages/f8cppsdk/src/service_bus.cpp`

### Studio (`f8pystudio`)

- Watch remote service KV: `packages/f8pystudio/f8pystudio/remote_state_watcher.py`
- Write remote state via endpoint: `packages/f8pystudio/f8pystudio/bridge/service_endpoint_client.py`
- Deploy rungraph (endpoint-only): `packages/f8pystudio/f8pystudio/deploy.py`

---

## Debug Cheatsheet

- Enable state debug logs: set `F8_STATE_DEBUG=1` (prints key events for rungraph apply, cross-state, publish_state)
- When propagation looks “wrong”, check:
  - KV write succeeded (bucket/key exists)
  - target field is writable (`F8StateAccess` + origin)
  - rungraph constraints (no state-edge cycles, no multi-upstream)

---

## FAQ (From Our Q&A / Common Developer Questions)

### How do we guarantee “synchronization” end-to-end?

We make synchronization a **contract**, not an emergent behavior:

- `set_rungraph` is request/reply and only persists `rungraph` after a successful apply.
- State uses KV as the source of truth; cross-state uses KV watch + initial get; intra-state propagation is staged and ordered.
- State edges are constrained (no cycles, no multi-upstream), so propagation is directional and predictable.

### How do we support a high-speed message stream and a reliable state system at the same time?

By splitting into two planes:

- Data Plane (Core Pub/Sub): fast fan-out streams, locally buffered by default.
- State Plane (JetStream KV): durable, watchable “current values”, editable and inspectable.

### How does fan-out work?

Cross-service data fan-out publishes **once** per `(serviceId, nodeId, outPort)` to a stable subject:

`svc.{serviceId}.nodes.{nodeId}.data.{portId}`

Multiple receivers subscribe to the same subject.

### How can users view and edit state?

- View: Studio watches KV buckets/keys via `RemoteStateWatcher`.
- Edit: Studio calls `svc.{serviceId}.set_state` (micro endpoint). The runtime validates and either rejects or persists to KV, then applies locally immediately.

### Is this a “decentralized CRDT on NATS KV”?

Not in the standard CRDT sense. JetStream KV acts as a centralized, ordered persistence layer, and we avoid multi-writer merges via rungraph topology constraints.

### What are the main hard parts in this design?

In practice:

- init order (avoid intermediate-state thrash),
- preventing loops and redundant updates,
- controlling backpressure under high-rate data producers,
- handling peers that are not ready yet,
- preventing exception/log spam in hot paths.

### What ensures the interaction feels instant?

Local state writes are applied immediately to `node.on_state(...)` (and intra-state fanout) without waiting for KV watch round-trips. KV watches then converge UI and other observers to the final state.

### Do we rely on `ts` as a strict total order?

For cross-state binding, we use `ts` primarily as a best-effort guard against out-of-order remote updates. Studio UI watchers typically dedupe by value and do not assume timestamps are globally monotonic.

### What happens if the upstream service bucket doesn’t exist yet?

KV watches are designed to keep running and retry in the background until the peer bucket/stream becomes available (instead of failing permanently).

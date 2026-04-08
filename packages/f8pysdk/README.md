## f8pysdk

Python runtime SDK for running an F8 service process.

### Core building blocks
- `ServiceBus` (`f8pysdk.bus`): public runtime facade over transport, routing, state, commands, and lifecycle.
- `ServiceHost` (`f8pysdk/service_host.py`): rungraph-driven runtime node materialization/registration.
- `ServiceRuntime` (`f8pysdk/service_runtime.py`): runtime facade that wires `ServiceBus` + `ServiceHost`.

Stable public modules:
- `f8pysdk.app`: service program/runtime/host entrypoints
- `f8pysdk.bus`: `ServiceBus`, `ServiceBusConfig`, and explicit bus component factory types
- `f8pysdk.specs`: generated protocol models, schema helpers, and spec metadata/edit-policy helpers
- `f8pysdk.nodes`: `RuntimeNode`, `ServiceNode`, `OperatorNode`
- `f8pysdk.registry`: `RuntimeNodeRegistry` and registry errors
- `f8pysdk.command`: command result/output policy types
- `f8pysdk.data`: data delivery and cross-publish policy types
- `f8pysdk.monitoring`: monitor snapshot collection/config types
- `f8pysdk.state`: canonical state read/write types
- `f8pysdk.transport`: NATS transport façade
- `f8pysdk.testing`: in-memory harness plus emit/pull/buffer test helpers

Explicit internal-only modules:
- `f8pysdk.service_bus.internal.*`: repo-internal runtime/test helpers owned by the ServiceBus runtime and not part of the public SDK API
  this replaces the earlier top-level pseudo-internal boundary so ownership stays with the runtime subsystem that defines these helpers

Removed deep legacy paths:
- old `f8pysdk.service_bus.*` compatibility shims such as `bus`, `codec`, `command_runtime`, `cross_state`, `domain.state_pipeline`, `error_utils`, `lifecycle`, `metadata`, `micro`, `payload`, `routing.data_*`, `routing_data`, `rungraph_apply`, `runtime_collections`, `state_publish`, `state_router`, and `state_store` have been removed from this repo
- repo code should import stable SDK modules such as `f8pysdk.bus`, `f8pysdk.specs`, `f8pysdk.command`, `f8pysdk.data`, `f8pysdk.state`, `f8pysdk.codec`, and `f8pysdk.testing` directly
- package root `f8pysdk` no longer wildcard-reexports generated types or helper functions; import from explicit owner modules instead

Internal runtime split:
- `DataRouter`: data routing, buffering, subscriptions, callback batching
- `StateStore`: state cache, access maps, KV-backed reads
- `StateRouter`: intra-state routing and cross-state watch lifecycle
- `CommandGateway`: canonical command execution path
- `service_bus.runtime`: canonical `ServiceBus` owner
- `service_bus.config`: canonical `ServiceBusConfig` owner
- `service_bus.data.*`: owner package for data runtime internals
- `service_bus.state.*`: owner package for state runtime internals
- `service_bus.state.options`: internal owner for `StatePublishOptions`
- `service_bus.state.pipeline`: state validation, persistence, and local-delivery owner
- `service_bus.workflow.metadata`: workflow-owned metadata builders
- `service_bus.internal.cache` / `service_bus.internal.logging`: shared runtime infrastructure helpers

Lifecycle contract:
- `ServiceBus` and `ServiceRuntime` are single-run objects.
- After `stop()`, create a new instance instead of calling `start()` again.

### Recommended service entrypoint
Prefer `ServiceApp` plus `Registry` for new services:
- one explicit owner object for describe/build/run/CLI
- one explicit registry owner for specs and runtime node factories
- standard CLI: `--describe`, `--service-id`, `--nats-url` (with `F8_SERVICE_ID`, `F8_NATS_URL` env fallbacks)

Minimal example:

```py
from f8pysdk.app import ServiceApp
from f8pysdk.registry import Registry
from mysvc.specs import MY_OPERATOR_SPEC, MY_SERVICE_SPEC
from mysvc.nodes import MyOperatorNode, MyServiceNode

registry = Registry()
registry.register_service(MY_SERVICE_SPEC, MyServiceNode)
registry.register_operator(MY_OPERATOR_SPEC, MyOperatorNode)

app = ServiceApp(
    service_class="f8.myservice",
    registry=registry,
)

app.run(service_id="svc-a", nats_url="nats://127.0.0.1:4222")
```

Registry contract:
- `Registry()` creates a fresh process-local runtime registry owner by default.
- Use `ServiceApp.build_shared_registry()` or `shared_runtime_node_registry()` only when process-global sharing is intentional.
- `Registry.register_service(spec, node_type_or_factory)` pairs describe-time metadata with runtime node construction in one call.
- `Registry.register_operator(spec, node_type_or_factory)` does the same for operators.
- Pass an explicit registry into `ServiceRuntime(...)` or `ServiceHost(...)` when multiple components must share registration state.
- Use `register_service_spec(...)` / `register_operator_spec(...)` for describe-time metadata.
- Use `register_service_factory(...)` / `register_operator_factory(...)` for runtime node construction.
- Missing operator runtime factories now fail explicitly; generic `ServiceNode` remains the default container when a service class has no custom service factory.

Compatibility note:
- repo entrypoints now use `ServiceApp`; new code should follow that single explicit app owner model.

Command contract:
- `ServiceBus.invoke_command(node_id, call, args, ...)` is the explicit local command API.
- `ServiceBus.invoke_command(...)` is reply-first by default and does not write hidden output state unless asked.
- Declared commands normalize scalar/list/dict args from their `F8Command.params` definition.
- Hidden command state fields remain supported as the graph-compatibility command adapter.
- Use `output_policy=CommandOutputPolicy.hidden_state` when a caller explicitly wants hidden output state writeback.

Data contract:
- `ServiceBus.emit_data(node_id, port, value, ...)` is the canonical public data-output API.
- `cross_publish_policy` controls network fan-out explicitly: `routed`, `all`, or `none`.
- `data_delivery` controls local consumer shape explicitly: `callback`, `buffered`, or explicit compatibility mode `both`.
- Default runtime behavior is `cross_publish_policy="routed"` and `data_delivery="callback"`.
- `ServiceBus.pull_data(node_id, port, ...)` reads local buffered inputs and may trigger same-service upstream compute when needed.
- Pull-triggered local compute satisfies local consumers only; internal typed emit options keep it from turning into hidden cross-service traffic.
- Monitor snapshots now expose routing counters in `frame`, including local-only emits, routed cross emits, suppressed cross publishes, callback deliveries, and buffered pull deliveries.
- Compatibility aliases remain limited to local delivery naming: `data_delivery="push"|"pull"|"both"`.

Stable helper modules:
- `f8pysdk.codec`: msgpack request/reply helpers (`encode_obj`, `decode_obj`, `decode_as`)
- `f8pysdk.specs`: generated protocol models plus schema/spec helpers
- `f8pysdk.state`: canonical state result/error/context types
- `f8pysdk.command`: command output/result enums and result envelopes
- `f8pysdk.data`: data delivery and cross-publish policy types
- `f8pysdk.monitoring`: monitor collector façade
- `f8pysdk.nodes`: runtime node base classes
- `f8pysdk.registry`: runtime registry and registry errors
- `f8pysdk.transport`: NATS transport façade and KV reset helpers
- `f8pysdk.testing`: in-memory harness plus emit/pull/buffer helpers for tests

### Headless Runner

Run a saved Studio session JSON without launching UI:

```bash
python -m f8pysdk.headless_runner --session path/to/session.json
```

Default behavior:
- attempts local NATS bootstrap (auto-start/download) when needed
- auto-starts discovered service processes
- deploys per-service rungraphs and waits until termination signal

Useful flags:
- `--no-bootstrap`: disable NATS auto bootstrap
- `--no-auto-start`: deploy only to already-running services

## f8pysdk

Python runtime SDK for running an F8 service process.

### Core building blocks
- `ServiceBus` (`f8pysdk/service_bus.py`): NATS + JetStream KV transport, routing tables, state cache, rungraph watch.
- `ServiceHost` (`f8pysdk/service_host.py`): rungraph-driven runtime node materialization/registration.
- `ServiceRuntime` (`f8pysdk/service_runtime.py`): runtime facade that wires `ServiceBus` + `ServiceHost`.

Lifecycle contract:
- `ServiceBus` and `ServiceRuntime` are single-run objects.
- After `stop()`, create a new instance instead of calling `start()` again.

### Recommended “fill-in-the-blanks” entrypoint
Use `ServiceCliTemplate` (`f8pysdk/service_cli.py`) to keep each service process consistent:
- standard CLI: `--describe`, `--service-id`, `--nats-url` (with `F8_SERVICE_ID`, `F8_NATS_URL` env fallbacks)
- fixed lifecycle hooks:
  - `register_specs(registry)` (required)
  - `setup(app)` (optional)
  - `teardown(app)` (optional)

Minimal example:

```py
from f8pysdk.service_cli import ServiceCliTemplate
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry

class MyService(ServiceCliTemplate):
    @property
    def service_class(self) -> str:
        return "f8.myservice"

    def register_specs(self, registry: RuntimeNodeRegistry) -> None:
        # registry.register_service(...)
        # registry.register(...)
        pass
```

Registry contract:
- `ServiceCliTemplate.build_registry()` returns a fresh `RuntimeNodeRegistry` by default.
- Use `ServiceCliTemplate.build_shared_registry()` only when process-global sharing is intentional.
- Pass an explicit registry into `ServiceRuntime(...)` or `ServiceHost(...)` when multiple components must share registration state.

Command contract:
- `ServiceBus.invoke_command(node_id, call, args, ...)` is the explicit local command API.
- `ServiceBus.invoke_command(...)` is reply-first by default and does not write hidden output state unless asked.
- Declared commands normalize scalar/list/dict args from their `F8Command.params` definition.
- Hidden command state fields remain supported as the graph-compatibility command adapter.
- Use `output_policy=CommandOutputPolicy.hidden_state` when a caller explicitly wants hidden output state writeback.

Data contract:
- `cross_publish_policy` controls network fan-out explicitly: `routed`, `all`, or `none`.
- `data_delivery` controls local consumer shape explicitly: `callback`, `buffered`, or explicit compatibility mode `both`.
- Default runtime behavior is `cross_publish_policy="routed"` and `data_delivery="callback"`.
- Pull-triggered local compute satisfies local consumers only; it no longer emits hidden cross-service traffic.
- Monitor snapshots now expose routing counters in `frame`, including local-only emits, routed cross emits, suppressed cross publishes, callback deliveries, and buffered pull deliveries.
- Legacy aliases remain accepted for migration: `publish_all_data=True|False`, `data_delivery="push"|"pull"|"both"`.

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

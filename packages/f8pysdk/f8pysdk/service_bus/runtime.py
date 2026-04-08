from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TYPE_CHECKING

from ..capabilities import (
    BusAttachableNode,
    ClosableNode,
    RungraphHook,
    ServiceHook,
    StatefulNode,
)
from ..command import CommandExecutionResult, CommandOutputPolicy
from ..data import CrossPublishPolicy, DataDeliveryMode
from ..generated import F8RuntimeGraph
from ..nats_naming import ensure_token, kv_bucket_for_service, kv_key_ready, kv_key_rungraph
from ..nats_transport import NatsTransport, NatsTransportConfig
from ..state import StateRead, StateWriteOrigin, StateWriteSource
from ..time_utils import now_ms
from .config import ServiceBusConfig, _debug_state_enabled
from .data.router import DataRouter
from .internal.command import CommandGateway, CommandInvocation, CommandInvokeOptions
from .monitor_collector import MonitorCollector, MonitorCollectorConfig
from .state.pipeline import publish_state as _publish_state_impl
from .state.router import StateRouter
from .state.store import StateStore
from .workflow.lifecycle import set_active as _set_active_impl
from .workflow.lifecycle import start as _start_impl
from .workflow.lifecycle import stop as _stop_impl
from .workflow.rungraph import set_rungraph as _set_rungraph_impl

if TYPE_CHECKING:
    from .internal.micro import ServiceBusMicroEndpoints


log = logging.getLogger(__name__)


def _coerce_cross_publish_policy(value: Any) -> CrossPublishPolicy | None:
    text = str(value or "").strip().lower()
    if text in ("routed", "all", "none"):
        return text
    return None


def _coerce_data_delivery_mode(value: Any) -> DataDeliveryMode | None:
    text = str(value or "").strip().lower()
    if text == "pull":
        return "buffered"
    if text == "push":
        return "callback"
    if text in ("buffered", "callback", "both"):
        return text
    return None


class _ServiceBusNode(StatefulNode, BusAttachableNode, Protocol):
    """
    Local-only node contract for ServiceBus registration.
    """


class ServiceBusComponentFactory(Protocol):
    """
    Explicit component builder for `ServiceBus` owner subsystems.

    This keeps component wiring out of `ServiceBus.__init__` so tests and
    future alternate runtimes can supply focused implementations explicitly.
    """

    def create_data_router(
        self,
        *,
        bus: "ServiceBus",
        cross_publish_policy: CrossPublishPolicy,
        data_delivery: DataDeliveryMode,
        input_max_buffers: int,
        default_queue_size: int,
    ) -> DataRouter: ...

    def create_state_store(
        self,
        *,
        bus: "ServiceBus",
        cache_max_entries: int,
    ) -> StateStore: ...

    def create_state_router(
        self,
        *,
        bus: "ServiceBus",
        store: StateStore,
    ) -> StateRouter: ...

    def create_command_gateway(
        self,
        *,
        bus: "ServiceBus",
        nodes: dict[str, _ServiceBusNode],
    ) -> CommandGateway: ...

    def create_monitor_collector(
        self,
        *,
        bus: "ServiceBus",
        config: MonitorCollectorConfig,
    ) -> MonitorCollector: ...


@dataclass(frozen=True)
class DefaultServiceBusComponentFactory:
    def create_data_router(
        self,
        *,
        bus: "ServiceBus",
        cross_publish_policy: CrossPublishPolicy,
        data_delivery: DataDeliveryMode,
        input_max_buffers: int,
        default_queue_size: int,
    ) -> DataRouter:
        return DataRouter(
            bus,
            cross_publish_policy=cross_publish_policy,
            data_delivery=data_delivery,
            input_max_buffers=input_max_buffers,
            default_queue_size=default_queue_size,
        )

    def create_state_store(
        self,
        *,
        bus: "ServiceBus",
        cache_max_entries: int,
    ) -> StateStore:
        return StateStore(bus, cache_max_entries=cache_max_entries)

    def create_state_router(
        self,
        *,
        bus: "ServiceBus",
        store: StateStore,
    ) -> StateRouter:
        return StateRouter(bus, store=store)

    def create_command_gateway(
        self,
        *,
        bus: "ServiceBus",
        nodes: dict[str, _ServiceBusNode],
    ) -> CommandGateway:
        return CommandGateway(bus=bus, nodes=nodes)

    def create_monitor_collector(
        self,
        *,
        bus: "ServiceBus",
        config: MonitorCollectorConfig,
    ) -> MonitorCollector:
        return MonitorCollector(bus, config)


class ServiceBus:
    """
    Service bus (clean, protocol-first).

    - Shared NATS connection (pub/sub + JetStream KV).
    - Rungraph updates are applied via micro endpoints.
    - Builds intra/cross routing tables for data edges.
    - Provides a shared state KV API for nodes.
    - Local data delivery is configured explicitly as buffered, callback, or both.
    - Pull-based consumers may trigger intra-service computation via `compute_output(...)`.
    """

    def __init__(
        self,
        config: ServiceBusConfig,
        *,
        transport: NatsTransport | None = None,
        component_factory: ServiceBusComponentFactory | None = None,
    ) -> None:
        self.service_id = ensure_token(config.service_id, label="service_id")
        self._service_name = str(config.service_name or "") or self.service_id
        self._service_class = str(config.service_class or "")
        self._debug_state = _debug_state_enabled()
        self._active = True
        self._ready = False
        cross_publish_policy = _coerce_cross_publish_policy(config.cross_publish_policy)
        if cross_publish_policy is None:
            if self._debug_state or log.isEnabledFor(logging.WARNING):
                log.warning("Invalid cross_publish_policy=%r; defaulting to 'routed'", config.cross_publish_policy)
            cross_publish_policy = "routed"
        mode = _coerce_data_delivery_mode(config.data_delivery)
        if mode is None:
            if self._debug_state or log.isEnabledFor(logging.WARNING):
                log.warning("Invalid data_delivery=%r; defaulting to 'callback'", config.data_delivery)
            mode = "callback"
        self._state_sync_concurrency = max(1, int(config.state_sync_concurrency))
        self._state_cache_max_entries = max(0, int(config.state_cache_max_entries))
        self._data_input_max_buffers = max(0, int(config.data_input_max_buffers))
        self._data_input_default_queue_size = max(1, int(config.data_input_default_queue_size))

        bucket = kv_bucket_for_service(self.service_id)
        if transport is None:
            self._transport = NatsTransport(
                NatsTransportConfig(
                    url=str(config.nats_url),
                    kv_bucket=str(bucket),
                    kv_storage=config.kv_storage,
                    delete_bucket_on_connect=bool(config.delete_bucket_on_start),
                    delete_bucket_on_close=bool(config.delete_bucket_on_stop),
                )
            )
        else:
            self._transport = transport

        self._nodes: dict[str, _ServiceBusNode] = {}
        self._graph: F8RuntimeGraph | None = None

        self._rungraph_key = kv_key_rungraph()
        self._ready_key = kv_key_ready()
        self._micro_endpoints: ServiceBusMicroEndpoints | None = None
        self._component_factory = component_factory if component_factory is not None else DefaultServiceBusComponentFactory()

        self._data_router = self._component_factory.create_data_router(
            bus=self,
            cross_publish_policy=cross_publish_policy,
            data_delivery=mode,
            input_max_buffers=self._data_input_max_buffers,
            default_queue_size=self._data_input_default_queue_size,
        )
        self._state_store = self._component_factory.create_state_store(
            bus=self,
            cache_max_entries=self._state_cache_max_entries,
        )
        self._state_router = self._component_factory.create_state_router(bus=self, store=self._state_store)
        self._command_gateway = self._component_factory.create_command_gateway(bus=self, nodes=self._nodes)

        self._rungraph_hooks: list[RungraphHook] = []
        self._service_hooks: list[ServiceHook] = []

        # Error dedupe for rungraph apply boundaries.
        self._rungraph_apply_error_once: set[str] = set()
        # Generic error dedupe for high-frequency paths (watchers/fanout/loops).
        self._error_once: set[str] = set()

        # Process-level termination request (set via `svc.<serviceId>.terminate`).
        # Service entrypoints may `await bus.wait_terminate()` to exit gracefully.
        self._terminate_event = asyncio.Event()
        self._monitor_collector = self._component_factory.create_monitor_collector(
            bus=self,
            config=MonitorCollectorConfig(
                enabled=bool(config.monitor_enabled),
                interval_ms=max(200, int(config.monitor_interval_ms)),
                window_ms=max(1000, int(config.monitor_window_ms)),
                gpu_enabled=bool(config.monitor_gpu_enabled),
            ),
        )
        if self._monitor_collector.enabled:
            self._monitor_record_emit = self._record_emit_metrics_enabled
            self._monitor_record_wait = self._record_wait_metrics_enabled
            self._monitor_record_input = self._record_input_metrics_enabled
            self._monitor_record_drop = self._record_drop_metrics_enabled
            self._monitor_record_local_only_emit = self._record_local_only_emit_metrics_enabled
            self._monitor_record_routed_cross_emit = self._record_routed_cross_emit_metrics_enabled
            self._monitor_record_suppressed_cross_publish = self._record_suppressed_cross_publish_metrics_enabled
            self._monitor_record_callback_delivery = self._record_callback_delivery_metrics_enabled
            self._monitor_record_buffer_pull_delivery = self._record_buffer_pull_delivery_metrics_enabled
        else:
            self._monitor_record_emit = self._noop_record_emit
            self._monitor_record_wait = self._noop_record_wait
            self._monitor_record_input = self._noop_record_input
            self._monitor_record_drop = self._noop_record_drop
            self._monitor_record_local_only_emit = self._noop_record_local_only_emit
            self._monitor_record_routed_cross_emit = self._noop_record_routed_cross_emit
            self._monitor_record_suppressed_cross_publish = self._noop_record_suppressed_cross_publish
            self._monitor_record_callback_delivery = self._noop_record_callback_delivery
            self._monitor_record_buffer_pull_delivery = self._noop_record_buffer_pull_delivery

        self._started = False
        self._closed = False

    async def wait_terminate(self) -> None:
        await self._terminate_event.wait()

    @staticmethod
    def _noop_record_emit(node_id: str, port: str, ts: int) -> None:
        del node_id, port, ts

    @staticmethod
    def _noop_record_wait(wait_ms: float) -> None:
        del wait_ms

    @staticmethod
    def _noop_record_input(node_id: str, port: str, ts: int) -> None:
        del node_id, port, ts

    @staticmethod
    def _noop_record_drop(dropped_count: int) -> None:
        del dropped_count

    @staticmethod
    def _noop_record_local_only_emit() -> None:
        return

    @staticmethod
    def _noop_record_routed_cross_emit() -> None:
        return

    @staticmethod
    def _noop_record_suppressed_cross_publish() -> None:
        return

    @staticmethod
    def _noop_record_callback_delivery() -> None:
        return

    @staticmethod
    def _noop_record_buffer_pull_delivery() -> None:
        return

    def _record_emit_metrics_enabled(self, node_id: str, port: str, ts: int) -> None:
        now_ts = int(now_ms())
        self._monitor_collector.record_processed(port=str(port), emit_ts_ms=int(ts), now_ts_ms=now_ts)
        self._monitor_collector.record_emit_completed(node_id=str(node_id), now_ts_ms=now_ts)

    def _record_wait_metrics_enabled(self, wait_ms: float) -> None:
        self._monitor_collector.record_wait_ms(wait_ms=wait_ms)

    def _record_input_metrics_enabled(self, node_id: str, port: str, ts: int) -> None:
        self._monitor_collector.record_observed(port=str(port))
        self._monitor_collector.record_input_sample_ts(node_id=str(node_id), sample_ts_ms=int(ts))

    def _record_drop_metrics_enabled(self, dropped_count: int) -> None:
        self._monitor_collector.record_dropped(dropped_count=int(dropped_count))

    def _record_local_only_emit_metrics_enabled(self) -> None:
        self._monitor_collector.record_local_only_emit()

    def _record_routed_cross_emit_metrics_enabled(self) -> None:
        self._monitor_collector.record_routed_cross_emit()

    def _record_suppressed_cross_publish_metrics_enabled(self) -> None:
        self._monitor_collector.record_suppressed_cross_publish()

    def _record_callback_delivery_metrics_enabled(self) -> None:
        self._monitor_collector.record_callback_delivery()

    def _record_buffer_pull_delivery_metrics_enabled(self) -> None:
        self._monitor_collector.record_buffer_pull_delivery()

    @property
    def cross_publish_policy(self) -> CrossPublishPolicy:
        return self._data_router.cross_publish_policy

    @property
    def data_delivery(self) -> DataDeliveryMode:
        return self._data_router.data_delivery

    @property
    def data_router(self) -> DataRouter:
        return self._data_router

    @property
    def state_store(self) -> StateStore:
        return self._state_store

    @property
    def state_router(self) -> StateRouter:
        return self._state_router

    def set_cross_publish_policy(self, value: Any, *, source: str = "service") -> None:
        policy = _coerce_cross_publish_policy(value)
        if policy is None:
            return
        if policy == self.cross_publish_policy:
            return
        self._data_router.set_cross_publish_policy(policy)
        if self._debug_state:
            print(f"state_debug[{self.service_id}] cross_publish_policy={policy} source={source}")

    def set_data_delivery(self, value: Any, *, source: str = "service") -> None:
        """
        Update data delivery behavior at runtime (service-controlled).
        """
        mode = _coerce_data_delivery_mode(value)
        if mode is None:
            return
        if mode == self.data_delivery:
            return
        self._data_router.set_data_delivery(mode)
        if self._debug_state:
            print(f"state_debug[{self.service_id}] data_delivery={mode} source={source}")

    def register_rungraph_hook(self, hook: RungraphHook) -> None:
        """
        Register a rungraph hook (called after validation + routing rebuild).
        """
        self._rungraph_hooks.append(hook)

    def unregister_rungraph_hook(self, hook: RungraphHook) -> None:
        reg = self._rungraph_hooks
        reg.remove(hook)

    def register_service_hook(self, hook: ServiceHook) -> None:
        """
        Register a service bus hook (ready/stop/activate/deactivate).
        """
        self._service_hooks.append(hook)

    def unregister_service_hook(self, hook: ServiceHook) -> None:
        reg = self._service_hooks
        reg.remove(hook)

    @property
    def active(self) -> bool:
        return bool(self._active)

    @property
    def command_gateway(self) -> CommandGateway:
        return self._command_gateway

    @property
    def monitor_collector(self) -> MonitorCollector:
        return self._monitor_collector

    async def set_active(
        self,
        active: bool,
        *,
        source: StateWriteSource | str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        await _set_active_impl(self, active, source=source, meta=meta)

    def register_node(self, node: _ServiceBusNode) -> None:
        node_id = ensure_token(node.node_id, label="node_id")
        self._nodes[node_id] = node
        node.attach(self)
        if self._graph is not None:
            self._command_gateway.refresh_bindings()

    def unregister_node(self, node_id: str) -> None:
        node_id = ensure_token(node_id, label="node_id")
        node = self._nodes.pop(node_id, None)
        self._data_router.remove_node_inputs(node_id)
        if self._graph is not None:
            self._command_gateway.refresh_bindings()
        if node is not None and isinstance(node, ClosableNode):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(node.close(), name=f"service_bus:close:{node_id}")
            except Exception as exc:
                log.debug("failed to schedule node close node_id=%s", node_id, exc_info=exc)

    def get_node(self, node_id: str) -> _ServiceBusNode | None:
        """
        Return the local runtime node instance if registered.
        """
        node_id = ensure_token(node_id, label="node_id")
        return self._nodes.get(node_id)

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("ServiceBus is not restartable after stop(); create a new instance")
        if self._started:
            return
        await _start_impl(self)
        self._started = True

    async def stop(self) -> None:
        if self._closed:
            return
        await _stop_impl(self)
        self._started = False
        self._closed = True

    async def subscribe_subject(
        self,
        subject: str,
        *,
        queue: str | None = None,
        cb: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> Any:
        return await self._data_router.subscribe_subject(subject, queue=queue, cb=cb)

    async def unsubscribe_subject(self, handle: Any) -> None:
        await self._data_router.unsubscribe_subject(handle)

    async def publish_state_external(
        self,
        node_id: str,
        field: str,
        value: Any,
        *,
        ts_ms: int | None = None,
        source: StateWriteSource | str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """
        Publish a state update as an external/user write.

        Canonical semantics:
        - persistence: the value is validated and written to KV/state cache
        - local delivery: same-process consumers are notified immediately
        - fanout: intra-service state edges fan out unless typed publish options disable them
        - cross-service propagation: remote services observe the persisted value through state watches

        This method intentionally does not allow callers to choose `origin`.
        `source` is allowed for diagnostics, but does not affect access control.
        """
        await _publish_state_impl(
            self,
            node_id,
            field,
            value,
            ts_ms=ts_ms,
            origin=StateWriteOrigin.external,
            source=source or StateWriteSource.endpoint,
            meta=dict(meta or {}),
        )

    async def publish_state_runtime(self, node_id: str, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        """
        Publish a runtime-owned state update through the same validated/persisted state chain.

        Use this for SDK/runtime writes that should persist and participate in
        the normal local state delivery/fanout behavior.
        """
        await _publish_state_impl(
            self,
            node_id,
            field,
            value,
            origin=StateWriteOrigin.runtime,
            source=StateWriteSource.runtime,
            ts_ms=ts_ms,
        )

    async def invoke_command(
        self,
        node_id: str,
        call: str,
        args: Any = None,
        *,
        meta: dict[str, Any] | None = None,
        output_policy: CommandOutputPolicy = CommandOutputPolicy.none,
        output_ts_ms: int | None = None,
        output_meta: dict[str, Any] | None = None,
    ) -> CommandExecutionResult:
        """
        Invoke a local registered commandable node through the canonical SDK command path.

        - Declared commands accept scalar/list/dict inputs and normalize them by parameter definition.
        - Undeclared commands require object-shaped args because there is no schema for positional mapping.
        - Defaults to reply-first behavior without hidden output state writeback.
        - `output_policy` controls whether hidden command output state is also written back.
        - The return value is structured so callers can inspect failures without parsing logs.
        """
        node_id_s = ensure_token(node_id, label="node_id")
        call_s = str(call or "").strip()
        if not call_s:
            raise ValueError("call is empty")
        return await self._command_gateway.invoke(
            invocation=CommandInvocation(node_id=node_id_s, call=call_s, args=args),
            options=CommandInvokeOptions(
                call_meta=dict(meta or {}),
                output_policy=output_policy,
                output_ts_ms=output_ts_ms,
                output_meta=dict(output_meta or {}),
            ),
        )

    async def get_state(self, node_id: str, field: str) -> StateRead:
        return await self._state_store.read_state(node_id, field)

    def get_state_cached(self, node_id: str, field: str, default: Any = None) -> Any:
        """
        Synchronous cached state snapshot read without KV/network IO.
        """
        return self._state_store.get_cached_value(node_id, field, default)

    async def set_rungraph(self, graph: F8RuntimeGraph) -> None:
        await _set_rungraph_impl(self, graph)

    async def publish(self, subject: str, payload: bytes) -> None:
        """Publish a message to a subject."""
        if not self._active:
            return
        await self._transport.publish(str(subject), bytes(payload))

    async def subscribe(
        self,
        subject: str,
        *,
        queue: str | None = None,
        cb: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> Any:
        """Subscribe to a subject."""
        return await self._transport.subscribe(str(subject), queue=queue, cb=cb)

    async def emit_data(self, node_id: str, port: str, value: Any, *, ts_ms: int | None = None) -> None:
        """
        Emit one output sample from a local node port.

        Canonical semantics:
        - local delivery: local routed consumers are satisfied first according to `data_delivery`
        - cross-service publish: controlled separately by `cross_publish_policy`
        - persistence: data samples are transient and are not written to KV state

        `emit_data(...)` is the public data-output path. Pull-triggered local
        recompute uses an internal local-only routing option so `pull_data(...)`
        never turns into hidden cross-service publication.
        """
        node_id_s = ensure_token(node_id, label="node_id")
        port_s = ensure_token(port, label="port_id")
        await self._data_router.emit_data(node_id_s, port_s, value, ts_ms=ts_ms)

    async def pull_data(self, node_id: str, port: str, *, ctx_id: str | int | None = None) -> Any:
        """
        Read the current buffered input value for a local node input port.

        In buffered modes this may trigger same-service upstream `compute_output(...)`
        when the input has no fresh sample yet. That recompute satisfies local
        consumers only; it does not publish cross-service data.
        """
        node_id_s = ensure_token(node_id, label="node_id")
        port_s = ensure_token(port, label="port_id")
        return await self._data_router.pull_data(node_id_s, port_s, ctx_id=ctx_id)

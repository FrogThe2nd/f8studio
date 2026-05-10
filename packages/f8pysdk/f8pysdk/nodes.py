from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, cast

from .capabilities import (
    BusAttachableNode,
    ComputableNode,
    DataReceivableNode,
    ExecutableNode,
    LifecycleNode,
    NodeBus,
    StatefulNode,
)
from .state import StateRead

if TYPE_CHECKING:
    from .generated import F8OperatorSpec


@dataclass
class RuntimeNode(BusAttachableNode, StatefulNode, DataReceivableNode, ComputableNode, LifecycleNode):
    """
    Base class for service runtime nodes.

    This is NOT a UI node. It's the runtime-side abstraction that receives
    inputs from intra/cross edges and emits outputs (fanout handled by runtime).

    Capabilities:
    - `BusAttachableNode` (attach to `ServiceBus`)
    - `StatefulNode` (optional state callback)
    - `ComputableNode` (optional pull-based compute)
    """

    node_id: str
    data_in_ports: list[str] = field(default_factory=list)
    data_out_ports: list[str] = field(default_factory=list)
    state_fields: list[str] = field(default_factory=list)

    _bus: NodeBus | None = field(default=None, init=False, repr=False)

    def attach(self, bus: Any) -> None:
        self._bus = cast(NodeBus, bus)

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        _ = field
        _ = value
        _ = ts_ms
        return

    async def validate_state(self, field: str, value: Any, *, ts_ms: int, meta: dict[str, Any]) -> Any:
        _ = field
        _ = ts_ms
        _ = meta
        return value

    async def on_data(self, port: str, value: Any, *, ts_ms: int | None = None) -> None:
        _ = port
        _ = value
        _ = ts_ms
        return

    async def on_lifecycle(self, active: bool, meta: dict[str, Any]) -> None:
        _ = active
        _ = meta
        return

    async def compute_output(self, port: str, ctx_id: str | int | None = None) -> Any:
        _ = port
        _ = ctx_id
        return None

    async def emit(
        self,
        port: str,
        value: Any,
        *,
        ts_ms: int | None = None,
        ctx_id: str | int | None = None,
    ) -> None:
        if self._bus is None:
            return
        await self._bus.emit_data(self.node_id, port, value, ts_ms=ts_ms, ctx_id=ctx_id)

    async def emit_exec(self, port: str, *, exec_id: str | int) -> None:
        if self._bus is None:
            return
        await self._bus.emit_exec(self.node_id, port, exec_id=exec_id)

    async def pull(self, port: str, *, ctx_id: str | int | None = None) -> Any:
        if self._bus is None:
            return None
        return await self._bus.pull_data(self.node_id, port, ctx_id=ctx_id)

    def input_zenoh_key(self, port: str) -> str | None:
        if self._bus is None:
            return None
        return self._bus.data_input_zenoh_key(self.node_id, port)

    def has_rungraph(self) -> bool:
        if self._bus is None:
            return False
        return self._bus.has_rungraph()

    async def set_state(
        self,
        field: str,
        value: Any,
        *,
        ts_ms: int | None = None,
        force_publish: bool = False,
    ) -> None:
        if self._bus is None:
            return
        if force_publish:
            await self._bus.publish_state_runtime(self.node_id, field, value, ts_ms=ts_ms, force_publish=True)
            return
        await self._bus.publish_state_runtime(self.node_id, field, value, ts_ms=ts_ms)

    async def report_error(
        self,
        code: str,
        message: str,
        severity: str = "error",
        fingerprint: str | None = None,
        ts_ms: int | None = None,
    ) -> None:
        if self._bus is None:
            return
        self._bus.report_error(
            self.node_id,
            code,
            message,
            severity=severity,
            fingerprint=fingerprint,
            ts_ms=ts_ms,
        )

    async def clear_error(self, fingerprint: str | None = None, ts_ms: int | None = None) -> None:
        if self._bus is None:
            return
        self._bus.clear_error(self.node_id, fingerprint=fingerprint, ts_ms=ts_ms)

    def record_monitor_processed(self, *, port: str, ts_ms: int | None = None) -> None:
        if self._bus is None:
            return
        self._bus.record_monitor_processed(port=port, ts_ms=ts_ms)

    def record_monitor_timing(
        self,
        *,
        port: str,
        process_ms: float,
        latency_ms: float,
        ts_ms: int | None = None,
    ) -> None:
        if self._bus is None:
            return
        self._bus.record_monitor_timing(
            port=port,
            process_ms=process_ms,
            latency_ms=latency_ms,
            ts_ms=ts_ms,
        )

    async def get_state(self, field: str) -> StateRead:
        if self._bus is None:
            return StateRead(found=False, value=None, ts_ms=None)
        return await self._bus.get_state(self.node_id, field)

    async def get_state_value(self, field: str) -> Any:
        return (await self.get_state(field)).value

    def get_state_cached(self, field: str, default: Any = None) -> Any:
        if self._bus is None:
            return default
        return self._bus.get_state_cached(self.node_id, str(field), default)


@dataclass
class ServiceNode(RuntimeNode):
    """
    Marker base class for service/container nodes.

    Service nodes typically expose lifecycle/commands/state and may provide data outputs.
    """


@dataclass
class OperatorNode(RuntimeNode, ExecutableNode):
    """
    Marker base class for operator nodes.

    Operator nodes are the executable/functional units within a service graph.
    """

    exec_in_ports: list[str] = field(default_factory=list)
    exec_out_ports: list[str] = field(default_factory=list)
    SPEC: ClassVar["F8OperatorSpec"]

    async def on_exec(self, exec_id: str | int, in_port: str | None = None) -> list[str]:
        _ = exec_id
        _ = in_port
        raise NotImplementedError(f"{type(self).__name__}.on_exec() is not implemented")


__all__ = ["OperatorNode", "RuntimeNode", "ServiceNode"]

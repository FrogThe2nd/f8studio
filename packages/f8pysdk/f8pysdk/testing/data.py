from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..generated import F8Edge

if TYPE_CHECKING:
    from ..service_bus import ServiceBus


async def emit_data(bus: "ServiceBus", node_id: str, port: str, value: Any, *, ts_ms: int | None = None) -> None:
    await bus.emit_data(node_id, port, value, ts_ms=ts_ms)


async def pull_data(bus: "ServiceBus", node_id: str, port: str, *, ctx_id: str | int | None = None) -> Any:
    return await bus.pull_data(node_id, port, ctx_id=ctx_id)


def push_input(bus: "ServiceBus", to_node: str, to_port: str, value: Any, *, ts_ms: int, edge: F8Edge | None = None) -> None:
    bus.data_router.push_input(to_node, to_port, value, ts_ms=ts_ms, edge=edge)


def buffer_input(
    bus: "ServiceBus",
    to_node: str,
    to_port: str,
    value: Any,
    *,
    ts_ms: int,
    edge: F8Edge | None,
    ctx_id: str | int | None,
) -> None:
    bus.data_router.buffer_input(
        to_node,
        to_port,
        value,
        ts_ms=ts_ms,
        edge=edge,
        ctx_id=ctx_id,
    )

from __future__ import annotations

from typing import TYPE_CHECKING

from ...generated import F8RuntimeGraph

if TYPE_CHECKING:
    from ..runtime import ServiceBus


def update_cross_state_bindings(bus: "ServiceBus", graph: F8RuntimeGraph) -> None:
    bus.state_router.update_cross_state_bindings(graph)


async def stop_unused_cross_state_watches(bus: "ServiceBus") -> None:
    await bus.state_router.stop_unused_watches()


async def sync_cross_state_watches(bus: "ServiceBus") -> None:
    await bus.state_router.sync_cross_state_watches()


async def on_remote_state_kv(
    bus: "ServiceBus",
    peer_service_id: str,
    key: str,
    value: bytes,
    *,
    is_initial: bool,
    no_fanout: bool = False,
) -> None:
    await bus.state_router.on_remote_state_kv(
        peer_service_id,
        key,
        value,
        is_initial=is_initial,
        no_fanout=no_fanout,
    )

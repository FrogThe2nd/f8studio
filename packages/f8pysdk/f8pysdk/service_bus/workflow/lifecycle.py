from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from ...capabilities import LifecycleNode
from ...time_utils import now_ms
from ..state.pipeline import publish_state
from ..internal.micro import ServiceBusMicroEndpoints
from ..state.write import StateWriteOrigin
from ..state.write import StateWriteSource
from ...codec import encode_obj
from .metadata import build_lifecycle_event_meta, build_lifecycle_state_meta

if TYPE_CHECKING:
    from ..api.bus import ServiceBus


log = logging.getLogger(__name__)


async def _ensure_micro_endpoints_started(bus: "ServiceBus") -> None:
    if bus._micro_endpoints is not None:
        return
    endpoints = ServiceBusMicroEndpoints(bus)
    bus._micro_endpoints = endpoints
    await endpoints.start()


async def _stop_micro_endpoints(bus: "ServiceBus") -> None:
    endpoints = bus._micro_endpoints
    if endpoints is not None:
        await endpoints.stop()
    bus._micro_endpoints = None


async def set_active(
    bus: "ServiceBus",
    active: bool,
    *,
    source: StateWriteSource | str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """
    Set service active state.

    - Persists `active` into KV under `nodes.<service_id>.state.active`
    - Notifies lifecycle nodes + service hooks (engine/executor can pause/resume)
    """
    await apply_active(bus, active, persist=True, source=source, meta=meta)


async def start(bus: "ServiceBus") -> None:
    # Reset termination latch for a fresh run.
    bus._terminate_event = asyncio.Event()
    await bus._transport.connect()
    if bus._monitor_collector.enabled:
        await bus._monitor_collector.start()
    # Clear any stale ready flag from a previous run as early as possible.
    await announce_ready(bus, False, reason="starting")
    if bus._micro_endpoints is None:
        await _ensure_micro_endpoints_started(bus)
    # Ensure lifecycle state always exists in KV, even when no service code writes it explicitly.
    await apply_active(
        bus,
        bool(bus._active),
        persist=True,
        source=StateWriteSource.system,
        meta={"bootstrap": True},
    )
    await notify_before_ready(bus)
    await announce_ready(bus, True, reason="start")
    await notify_after_ready(bus)


async def stop(bus: "ServiceBus") -> None:
    await notify_before_stop(bus)
    await announce_ready(bus, False, reason="stop")

    await _stop_micro_endpoints(bus)
    await bus.data_router.stop()
    await bus.state_router.stop()
    bus.state_store.clear_cache()
    bus.state_store.clear_access_map()

    if bus._monitor_collector.enabled:
        await bus._monitor_collector.stop()
    await bus._transport.close()
    await notify_after_stop(bus)
    bus._rungraph_hooks.clear()
    bus._service_hooks.clear()


async def announce_ready(bus: "ServiceBus", ready: bool, *, reason: str) -> None:
    bus._ready = bool(ready)
    if bus._monitor_collector.enabled:
        bus._monitor_collector.record_ready(bool(ready))
    payload = {
        "serviceId": bus.service_id,
        "ready": bool(ready),
        "reason": str(reason or ""),
        "ts": int(now_ms()),
    }
    raw = encode_obj(payload)
    await bus._transport.kv_put(bus._ready_key, raw)


async def notify_before_ready(bus: "ServiceBus") -> None:
    for hook in list(bus._service_hooks):
        try:
            r = hook.on_before_ready(bus)
            if asyncio.iscoroutine(r):
                await r
        except Exception as exc:
            log.error("service hook failed: on_before_ready %s", type(hook).__name__, exc_info=exc)


async def notify_after_ready(bus: "ServiceBus") -> None:
    for hook in list(bus._service_hooks):
        try:
            r = hook.on_after_ready(bus)
            if asyncio.iscoroutine(r):
                await r
        except Exception as exc:
            log.error("service hook failed: on_after_ready %s", type(hook).__name__, exc_info=exc)


async def notify_before_stop(bus: "ServiceBus") -> None:
    for hook in list(bus._service_hooks):
        try:
            r = hook.on_before_stop(bus)
            if asyncio.iscoroutine(r):
                await r
        except Exception as exc:
            log.error("service hook failed: on_before_stop %s", type(hook).__name__, exc_info=exc)


async def notify_after_stop(bus: "ServiceBus") -> None:
    for hook in list(bus._service_hooks):
        try:
            r = hook.on_after_stop(bus)
            if asyncio.iscoroutine(r):
                await r
        except Exception as exc:
            log.error("service hook failed: on_after_stop %s", type(hook).__name__, exc_info=exc)


async def apply_active(
    bus: "ServiceBus", active: bool, *, persist: bool, source: StateWriteSource | str | None, meta: dict[str, Any] | None
) -> None:
    active = bool(active)

    changed = active != bus._active
    bus._active = active

    payload = build_lifecycle_event_meta(source=source, meta=meta)

    # Apply lifecycle change to local nodes/hooks first so pause/resume takes effect
    # with minimal latency; persist `active` state right after.
    if changed:
        for node in list(bus._nodes.values()):
            if not isinstance(node, LifecycleNode):
                continue
            r = node.on_lifecycle(bool(active), dict(payload))
            if asyncio.iscoroutine(r):
                await r

        for hook in list(bus._service_hooks):
            try:
                if bool(active):
                    r = hook.on_activate(bus, dict(payload))
                else:
                    r = hook.on_deactivate(bus, dict(payload))
                if asyncio.iscoroutine(r):
                    await r
            except Exception as exc:
                phase = "on_activate" if bool(active) else "on_deactivate"
                log.error("service hook failed: %s %s", phase, type(hook).__name__, exc_info=exc)

    if persist:
        await publish_state(
            bus,
            bus.service_id,
            "active",
            bool(active),
            origin=StateWriteOrigin.runtime,
            source=source or StateWriteSource.runtime,
            meta=build_lifecycle_state_meta(meta=meta),
        )

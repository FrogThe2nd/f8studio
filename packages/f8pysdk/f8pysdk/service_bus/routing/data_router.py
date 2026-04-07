from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...capabilities import ComputableNode, DataReceivableNode
from ...generated import F8Edge, F8EdgeStrategyEnum
from ...nats_naming import data_subject
from ...time_utils import now_ms
from ..api.config import CrossPublishPolicy, DataDeliveryMode
from ..codec import decode_obj, encode_obj
from ..error_utils import log_error_once
from ..runtime_collections import CappedOrderedDict
from .data_emit import CrossPublishPlan, DataEmitOptions

if TYPE_CHECKING:
    from ..api.bus import ServiceBus


log = logging.getLogger(__name__)

DataRouteTarget = tuple[str, str, F8Edge]
DataOutRoutes = dict[tuple[str, str], tuple[DataRouteTarget, ...]]
DataCrossInRoutes = dict[str, tuple[DataRouteTarget, ...]]
DataCrossOutRoutes = dict[tuple[str, str], str]


@dataclass
class InputBuffer:
    to_node: str
    to_port: str
    edge: F8Edge | None
    queue: deque[tuple[Any, int]] = field(default_factory=deque)
    last_seen_value: Any = None
    last_seen_ts: int | None = None
    last_seen_ctx_id: str | int | None = None
    last_pulled_value: Any = None
    last_pulled_ts: int | None = None
    last_pulled_ctx_id: str | int | None = None


class DataRouter:
    def __init__(
        self,
        bus: "ServiceBus",
        *,
        cross_publish_policy: CrossPublishPolicy,
        data_delivery: DataDeliveryMode,
        input_max_buffers: int,
        default_queue_size: int,
    ) -> None:
        self._bus = bus
        self._cross_publish_policy = cross_publish_policy
        self._data_delivery = data_delivery
        self._default_queue_size = max(1, int(default_queue_size))
        self._intra_data_out: DataOutRoutes = {}
        self._intra_data_in: DataOutRoutes = {}
        self._cross_in_by_subject: DataCrossInRoutes = {}
        self._cross_out_subjects: DataCrossOutRoutes = {}
        self._inputs: CappedOrderedDict[tuple[str, str], InputBuffer] = CappedOrderedDict(
            max_entries=max(0, int(input_max_buffers))
        )
        self._route_subscriptions: dict[str, Any] = {}
        self._custom_subscriptions: list[Any] = []
        self._on_data_push_queue: deque[tuple[str, str, Any, int]] = deque()
        self._on_data_flush_task: asyncio.Task[None] | None = None

    @property
    def cross_publish_policy(self) -> CrossPublishPolicy:
        return self._cross_publish_policy

    @property
    def data_delivery(self) -> DataDeliveryMode:
        return self._data_delivery

    @property
    def input_buffers(self) -> CappedOrderedDict[tuple[str, str], InputBuffer]:
        return self._inputs

    @property
    def intra_data_out(self) -> DataOutRoutes:
        return self._intra_data_out

    @property
    def intra_data_in(self) -> DataOutRoutes:
        return self._intra_data_in

    @property
    def cross_in_by_subject(self) -> DataCrossInRoutes:
        return self._cross_in_by_subject

    @property
    def cross_out_subjects(self) -> DataCrossOutRoutes:
        return self._cross_out_subjects

    def set_cross_publish_policy(self, policy: CrossPublishPolicy) -> None:
        self._cross_publish_policy = policy

    def set_data_delivery(self, mode: DataDeliveryMode) -> None:
        self._data_delivery = mode

    def remove_node_inputs(self, node_id: str) -> None:
        node_id_s = str(node_id)
        for key in [item for item in self._inputs.keys() if item[0] == node_id_s]:
            self._inputs.pop(key, None)

    def queue_depth(self) -> int:
        depth = 0
        for buf in list(self._inputs.values()):
            try:
                depth += int(len(buf.queue))
            except (AttributeError, RuntimeError, TypeError):
                continue
        if depth < 0:
            return 0
        return int(depth)

    async def replace_routes(
        self,
        *,
        intra_data_out: DataOutRoutes,
        intra_data_in: DataOutRoutes,
        cross_in_by_subject: DataCrossInRoutes,
        cross_out_subjects: DataCrossOutRoutes,
    ) -> None:
        self._inputs.clear()
        self._intra_data_out = dict(intra_data_out)
        self._intra_data_in = dict(intra_data_in)
        self._cross_in_by_subject = dict(cross_in_by_subject)
        self._cross_out_subjects = dict(cross_out_subjects)
        self.precreate_input_buffers_for_cross_in(self._cross_in_by_subject)
        await self.sync_subscriptions(set(self._cross_in_by_subject.keys()))

    async def stop(self) -> None:
        for sub in list(self._custom_subscriptions):
            await sub.unsubscribe()
        self._custom_subscriptions.clear()

        for sub in list(self._route_subscriptions.values()):
            await sub.unsubscribe()
        self._route_subscriptions.clear()

        self._cross_in_by_subject.clear()
        self._intra_data_out.clear()
        self._intra_data_in.clear()
        self._cross_out_subjects.clear()
        self._inputs.clear()
        self._on_data_push_queue.clear()
        await self._stop_flush_task()

    async def emit_data(
        self,
        node_id: str,
        port: str,
        value: Any,
        *,
        ts_ms: int | None = None,
        options: DataEmitOptions | None = None,
    ) -> None:
        bus = self._bus
        if not bus._active:
            return
        emit_options = options or DataEmitOptions()
        ts = int(ts_ms or now_ms())
        bus._monitor_record_emit(str(node_id), str(port), int(ts))
        await self._route_emitted_value(
            from_node=str(node_id),
            from_port=str(port),
            value=value,
            ts_ms=ts,
            ctx_id=None,
            options=emit_options,
        )

    async def pull_data(self, node_id: str, port: str, *, ctx_id: str | int | None = None) -> Any:
        bus = self._bus
        if not bus._active:
            return None
        buf = self._ensure_input_buffer(to_node=node_id, to_port=port, edge=None)
        edge = buf.edge
        now_ts = int(now_ms())

        last_seen_ts = int(buf.last_seen_ts or now_ts)
        if self.is_stale(edge, last_seen_ts):
            return None

        strategy = edge.strategy if edge is not None else F8EdgeStrategyEnum.latest
        if not isinstance(strategy, F8EdgeStrategyEnum):
            strategy = F8EdgeStrategyEnum.latest

        if strategy == F8EdgeStrategyEnum.queue:
            if not buf.queue:
                if ctx_id is None or buf.last_seen_ctx_id != ctx_id:
                    await self.ensure_input_available(node_id=node_id, port=port, ctx_id=ctx_id)
                if not buf.queue:
                    return None
            value, ts = buf.queue.popleft()
            if ts is not None:
                wait_ms = float(max(0, int(now_ms()) - int(ts)))
                bus._monitor_record_wait(wait_ms)
            buf.last_pulled_value = value
            buf.last_pulled_ts = int(ts) if ts is not None else now_ts
            buf.last_pulled_ctx_id = ctx_id
            bus._monitor_record_buffer_pull_delivery()
            return value

        if not buf.queue and (ctx_id is None or buf.last_seen_ctx_id != ctx_id):
            await self.ensure_input_available(node_id=node_id, port=port, ctx_id=ctx_id)
        value = buf.last_seen_value
        if buf.last_seen_ts is not None:
            wait_ms = float(max(0, int(now_ms()) - int(buf.last_seen_ts)))
            bus._monitor_record_wait(wait_ms)
        buf.queue.clear()
        if value is not None:
            buf.last_pulled_value = value
            buf.last_pulled_ts = now_ts
            buf.last_pulled_ctx_id = ctx_id
            bus._monitor_record_buffer_pull_delivery()
        return value

    async def ensure_input_available(self, *, node_id: str, port: str, ctx_id: str | int | None = None) -> None:
        bus = self._bus
        if not bus._graph:
            return

        upstream = self._intra_data_in.get((node_id, port)) or ()
        if not upstream:
            return

        stack: set[tuple[str, str]] = set()
        await self.compute_and_buffer_for_input(node_id=node_id, port=port, ctx_id=ctx_id, stack=stack)

    async def compute_and_buffer_for_input(
        self,
        *,
        node_id: str,
        port: str,
        ctx_id: str | int | None,
        stack: set[tuple[str, str]],
    ) -> None:
        bus = self._bus
        key = (str(node_id), str(port))
        if key in stack:
            return
        stack.add(key)
        try:
            local_compute_options = DataEmitOptions.local_compute_only()
            for from_node, from_port, edge in self._intra_data_in.get(key) or ():
                src = bus._nodes.get(from_node)
                if src is None:
                    continue
                try:
                    if isinstance(src, ComputableNode):
                        value = await src.compute_output(from_port, ctx_id=ctx_id)
                    else:
                        value = None
                except Exception as exc:
                    log_error_once(
                        bus,
                        key=f"compute_output_failed:{from_node}:{from_port}",
                        message=f"compute_output failed for {from_node}.{from_port}",
                        exc=exc,
                    )
                    continue
                if value is None:
                    continue

                ts_now = int(now_ms())
                delivered = await self._route_emitted_value(
                    from_node=str(from_node),
                    from_port=str(from_port),
                    value=value,
                    ts_ms=ts_now,
                    ctx_id=ctx_id,
                    options=local_compute_options,
                    force_buffer_target=key,
                )
                if delivered:
                    continue
                self._deliver_local_input(
                    to_node=node_id,
                    to_port=port,
                    value=value,
                    ts_ms=ts_now,
                    edge=edge,
                    ctx_id=ctx_id,
                    force_buffer=True,
                )
        finally:
            stack.discard(key)

    async def on_cross_data_msg(self, subject: str, payload: bytes) -> None:
        bus = self._bus
        if not bus._active:
            return
        targets = self._cross_in_by_subject.get(str(subject)) or []
        if not targets:
            return
        value: Any = None
        ts: int | None = None
        try:
            msg = decode_obj(payload)
            value = msg.get("value")
            ts = msg.get("ts")
        except ValueError:
            value = payload

        ts_i = int(ts) if ts is not None else int(now_ms())
        for to_node, to_port, edge in targets:
            try:
                if self.is_stale(edge, ts_i):
                    continue
                self.push_input(to_node, to_port, value, ts_ms=ts_i, edge=edge)
            except Exception as exc:
                log_error_once(
                    bus,
                    key=f"cross_data_push_failed:{to_node}:{to_port}",
                    message=f"cross-data delivery failed for {to_node}.{to_port}",
                    exc=exc,
                )

    @staticmethod
    def is_stale(edge: F8Edge | None, ts_ms: int) -> bool:
        if edge is None:
            return False
        try:
            timeout = edge.timeoutMs
            if timeout is None:
                return False
            timeout_i = int(timeout)
            if timeout_i <= 0:
                return False
            return (now_ms() - int(ts_ms)) > timeout_i
        except (AttributeError, TypeError, ValueError):
            return False

    def push_input(self, to_node: str, to_port: str, value: Any, *, ts_ms: int, edge: F8Edge | None = None) -> None:
        self._deliver_local_input(
            to_node=to_node,
            to_port=to_port,
            value=value,
            ts_ms=int(ts_ms),
            edge=edge,
            ctx_id=None,
        )

    def buffer_input(
        self,
        to_node: str,
        to_port: str,
        value: Any,
        *,
        ts_ms: int,
        edge: F8Edge | None,
        ctx_id: str | int | None,
    ) -> None:
        bus = self._bus
        buf = self._ensure_input_buffer(to_node=to_node, to_port=to_port, edge=edge)

        buf.last_seen_value = value
        buf.last_seen_ts = int(ts_ms)
        buf.last_seen_ctx_id = ctx_id
        bus._monitor_record_input(str(to_node), str(to_port), int(ts_ms))

        buf.queue.append((value, int(ts_ms)))
        max_n = self._default_queue_size
        if buf.edge is not None:
            try:
                max_n = max(1, int(buf.edge.queueSize))
            except (AttributeError, TypeError, ValueError):
                max_n = self._default_queue_size
        dropped_count = 0
        if len(buf.queue) > max_n:
            while len(buf.queue) > max_n:
                buf.queue.popleft()
                dropped_count += 1
        if dropped_count > 0:
            bus._monitor_record_drop(int(dropped_count))

    async def sync_subscriptions(self, want_subjects: set[str]) -> None:
        bus = self._bus
        for subject in list(self._route_subscriptions.keys()):
            if subject in want_subjects:
                continue
            sub = self._route_subscriptions.pop(subject, None)
            if sub is None:
                continue
            try:
                await sub.unsubscribe()
            except Exception as exc:
                log.error("failed to unsubscribe routed subject=%s", subject, exc_info=exc)

        for subject in want_subjects:
            if subject in self._route_subscriptions:
                continue

            async def _cb(s: str, p: bytes) -> None:
                await self.on_cross_data_msg(s, p)

            handle = await bus._transport.subscribe(subject, cb=_cb)
            self._route_subscriptions[subject] = handle

    async def subscribe_subject(
        self,
        subject: str,
        *,
        queue: str | None = None,
        cb: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> Any:
        subject_s = str(subject or "").strip()
        if not subject_s:
            raise ValueError("subject must be non-empty")
        handle = await self._bus._transport.subscribe(subject_s, queue=str(queue) if queue else None, cb=cb)
        self._custom_subscriptions.append(handle)
        return handle

    async def unsubscribe_subject(self, handle: Any) -> None:
        if handle is None:
            return
        await handle.unsubscribe()
        if handle in self._custom_subscriptions:
            self._custom_subscriptions.remove(handle)

    def precreate_input_buffers_for_cross_in(self, cross_in: DataCrossInRoutes) -> None:
        for targets in cross_in.values():
            for to_node, to_port, edge in targets:
                self._ensure_input_buffer(
                    to_node=str(to_node),
                    to_port=str(to_port),
                    edge=edge,
                )

    def _ensure_input_buffer(self, *, to_node: str, to_port: str, edge: F8Edge | None) -> InputBuffer:
        key = (to_node, to_port)
        buf = self._inputs.get(key)
        if buf is None:
            buf = InputBuffer(to_node=to_node, to_port=to_port, edge=edge)
            self._inputs[key] = buf
        if edge is not None:
            buf.edge = edge
        return buf

    def _buffers_data_locally(self) -> bool:
        return self._data_delivery in ("buffered", "both")

    def _callbacks_data_locally(self) -> bool:
        return self._data_delivery in ("callback", "both")

    def _enqueue_on_data_callback(self, *, to_node: str, to_port: str, value: Any, ts_ms: int) -> None:
        bus = self._bus
        bus._monitor_record_callback_delivery()
        self._on_data_push_queue.append((to_node, to_port, value, int(ts_ms)))
        task = self._on_data_flush_task
        if task is None or task.done():
            try:
                loop = asyncio.get_running_loop()
                self._on_data_flush_task = loop.create_task(
                    self._flush_on_data_push_queue(),
                    name=f"service_bus:on_data_flush:{bus.service_id}",
                )
            except Exception as exc:
                log_error_once(
                    bus,
                    key=f"push_on_data_schedule_failed:{to_node}:{to_port}",
                    message=f"failed to schedule on_data for {to_node}.{to_port}",
                    exc=exc,
                )

    def _deliver_local_input(
        self,
        *,
        to_node: str,
        to_port: str,
        value: Any,
        ts_ms: int,
        edge: F8Edge | None,
        ctx_id: str | int | None,
        force_buffer: bool = False,
    ) -> None:
        bus = self._bus
        if force_buffer or self._buffers_data_locally():
            self.buffer_input(
                to_node=to_node,
                to_port=to_port,
                value=value,
                ts_ms=int(ts_ms),
                edge=edge,
                ctx_id=ctx_id,
            )
        elif self._callbacks_data_locally():
            bus._monitor_record_input(str(to_node), str(to_port), int(ts_ms))
        if self._callbacks_data_locally():
            self._enqueue_on_data_callback(to_node=to_node, to_port=to_port, value=value, ts_ms=int(ts_ms))

    async def _route_emitted_value(
        self,
        *,
        from_node: str,
        from_port: str,
        value: Any,
        ts_ms: int,
        ctx_id: str | int | None,
        options: DataEmitOptions,
        force_buffer_target: tuple[str, str] | None = None,
    ) -> bool:
        delivered = self._fanout_local_routes(
            from_node=from_node,
            from_port=from_port,
            value=value,
            ts_ms=int(ts_ms),
            ctx_id=ctx_id,
            options=options,
            force_buffer_target=force_buffer_target,
        )
        plan = self._cross_publish_plan(node_id=from_node, port=from_port)
        if options.publish_cross_service and plan.will_publish:
            payload = encode_obj({"value": value, "ts": int(ts_ms)})
            await self._bus._transport.publish(plan.subject, payload)
            self._bus._monitor_record_routed_cross_emit()
            return delivered
        self._record_skipped_cross_publish(plan=plan, publish_enabled=options.publish_cross_service)
        return delivered

    def _fanout_local_routes(
        self,
        *,
        from_node: str,
        from_port: str,
        value: Any,
        ts_ms: int,
        ctx_id: str | int | None,
        options: DataEmitOptions,
        force_buffer_target: tuple[str, str] | None,
    ) -> bool:
        if not options.deliver_local:
            return False
        delivered = False
        for to_node, to_port, edge in self._intra_data_out.get((from_node, from_port), ()):
            self._deliver_local_input(
                to_node=to_node,
                to_port=to_port,
                value=value,
                ts_ms=int(ts_ms),
                edge=edge,
                ctx_id=ctx_id,
                force_buffer=force_buffer_target == (str(to_node), str(to_port)),
            )
            delivered = True
        return delivered

    def _record_skipped_cross_publish(self, *, plan: CrossPublishPlan, publish_enabled: bool) -> None:
        bus = self._bus
        if publish_enabled:
            if plan.decision == "suppressed":
                bus._monitor_record_suppressed_cross_publish()
            elif plan.decision == "local_only":
                bus._monitor_record_local_only_emit()
            return
        if plan.decision in ("publish", "suppressed"):
            bus._monitor_record_suppressed_cross_publish()

    def _cross_publish_plan(self, *, node_id: str, port: str) -> CrossPublishPlan:
        if self._cross_publish_policy == "none":
            if (node_id, port) in self._cross_out_subjects:
                return CrossPublishPlan(subject="", decision="suppressed")
            return CrossPublishPlan(subject="", decision="local_only")
        if self._cross_publish_policy == "all":
            return CrossPublishPlan(
                subject=data_subject(self._bus.service_id, from_node_id=node_id, port_id=port),
                decision="publish",
            )
        subject = self._cross_out_subjects.get((node_id, port)) or ""
        if subject:
            return CrossPublishPlan(subject=subject, decision="publish")
        return CrossPublishPlan(subject="", decision="local_only")

    async def _flush_on_data_push_queue(self) -> None:
        try:
            while self._on_data_push_queue:
                batch: list[tuple[str, str, Any, int]] = []
                while self._on_data_push_queue:
                    batch.append(self._on_data_push_queue.popleft())
                coalesced: dict[tuple[str, str], tuple[Any, int]] = {}
                for node_id, port, value, ts_ms in batch:
                    coalesced[(node_id, port)] = (value, int(ts_ms))
                for (node_id, port), (value, ts_ms) in coalesced.items():
                    node = self._bus._nodes.get(node_id)
                    if node is None or not isinstance(node, DataReceivableNode):
                        continue
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(
                            node.on_data(port, value, ts_ms=ts_ms),  # type: ignore[misc]
                            name=f"service_bus:on_data:{node_id}:{port}",
                        )
                    except Exception as exc:
                        log_error_once(
                            self._bus,
                            key=f"push_on_data_schedule_failed:{node_id}:{port}",
                            message=f"failed to schedule on_data for {node_id}.{port}",
                            exc=exc,
                        )
                await asyncio.sleep(0)
        finally:
            self._on_data_flush_task = None

    async def _stop_flush_task(self) -> None:
        flush_task = self._on_data_flush_task
        self._on_data_flush_task = None
        if flush_task is not None:
            flush_task.cancel()
            try:
                await flush_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.error("on_data flush task stop failed", exc_info=exc)

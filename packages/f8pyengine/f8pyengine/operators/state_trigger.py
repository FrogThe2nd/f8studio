from __future__ import annotations

import asyncio
import logging
from typing import Any

from f8pysdk import (
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8StateAccess,
    F8StateSpec,
    any_schema,
    boolean_schema,
)
from f8pysdk.capabilities import EntrypointNode
from f8pysdk.executors.exec_flow import EntrypointContext
from f8pysdk.json_unwrap import unwrap_json_value as _unwrap_json_value
from f8pysdk.nats_naming import ensure_token
from f8pysdk.runtime_node import OperatorNode
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry

from ..constants import SERVICE_CLASS

OPERATOR_CLASS = "f8.state_trigger"
logger = logging.getLogger(__name__)


class StateTriggerRuntimeNode(OperatorNode, EntrypointNode):
    """
    Event-driven state watcher that emits exec when `value` changes.
    """

    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[p.name for p in (node.dataInPorts or [])],
            data_out_ports=[p.name for p in (node.dataOutPorts or [])],
            state_fields=[s.name for s in (node.stateFields or [])],
            exec_in_ports=[str(p) for p in (node.execInPorts or [])],
            exec_out_ports=[str(p) for p in (node.execOutPorts or [])],
        )
        self._initial_state = dict(initial_state or {})
        self._enabled = self._coerce_bool(_unwrap_json_value(self._initial_state.get("enabled")), default=True)
        self._fire_on_start = self._coerce_bool(_unwrap_json_value(self._initial_state.get("fireOnStart")), default=False)
        self._has_last_value = "value" in self._initial_state
        self._last_value = _unwrap_json_value(self._initial_state.get("value"))

        self._entrypoint_ctx: EntrypointContext | None = None
        self._pending_exec_id: str | int | None = None
        self._emit_wakeup = asyncio.Event()
        self._emit_task: asyncio.Task[None] | None = None
        self._emit_seq = 0

    async def on_exec(self, _exec_id: str | int, _in_port: str | None = None) -> list[str]:
        return ["changed"]

    async def start_entrypoint(self, ctx: EntrypointContext) -> None:
        self._entrypoint_ctx = ctx
        if self._enabled and self._fire_on_start and self._has_last_value:
            self._emit_seq += 1
            self._request_exec_emit(exec_id=int(self._emit_seq))

    async def stop_entrypoint(self) -> None:
        self._entrypoint_ctx = None
        await self._cancel_emit_task()

    async def validate_state(
        self, field: str, value: Any, *, ts_ms: int | None = None, meta: dict[str, Any] | None = None
    ) -> Any:
        _ = ts_ms
        _ = meta
        name = str(field or "").strip()
        raw_value = _unwrap_json_value(value)
        if name == "enabled":
            return self._coerce_bool(raw_value, default=True)
        if name == "fireOnStart":
            return self._coerce_bool(raw_value, default=False)
        if name == "value":
            return raw_value
        return value

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        _ = ts_ms
        name = str(field or "").strip()
        if name == "enabled":
            self._enabled = self._coerce_bool(_unwrap_json_value(value), default=self._enabled)
            return
        if name == "fireOnStart":
            self._fire_on_start = self._coerce_bool(_unwrap_json_value(value), default=self._fire_on_start)
            return
        if name != "value":
            return

        new_value = _unwrap_json_value(value)
        changed = (not self._has_last_value) or (not self._values_equal(self._last_value, new_value))
        self._last_value = new_value
        self._has_last_value = True
        if not changed or (not self._enabled):
            return

        self._emit_seq += 1
        self._request_exec_emit(exec_id=int(self._emit_seq))

    def _request_exec_emit(self, *, exec_id: str | int) -> None:
        if self._entrypoint_ctx is None:
            return
        self._pending_exec_id = exec_id
        self._emit_wakeup.set()
        task = self._emit_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._emit_task = loop.create_task(
            self._emit_exec_loop(),
            name=f"state_trigger:emit_exec:{self.node_id}",
        )

    async def _emit_exec_loop(self) -> None:
        try:
            while True:
                await self._emit_wakeup.wait()
                self._emit_wakeup.clear()
                exec_id = self._pending_exec_id
                self._pending_exec_id = None
                if exec_id is None:
                    continue
                ctx = self._entrypoint_ctx
                if ctx is None:
                    continue
                try:
                    await ctx.emit_exec("changed", exec_id=exec_id)
                except Exception as exc:
                    logger.exception("[%s:state_trigger] emit exec failed", self.node_id, exc_info=exc)
        except asyncio.CancelledError:
            raise
        finally:
            self._emit_task = None

    async def _cancel_emit_task(self) -> None:
        task = self._emit_task
        self._emit_task = None
        self._pending_exec_id = None
        self._emit_wakeup.clear()
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("[%s:state_trigger] stop emit task failed", self.node_id, exc_info=exc)

    @staticmethod
    def _values_equal(left: Any, right: Any) -> bool:
        try:
            return bool(left == right)
        except Exception:
            return False

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
        return bool(default)


StateTriggerRuntimeNode.SPEC = F8OperatorSpec(
    schemaVersion=F8OperatorSchemaVersion.f8operator_1,
    serviceClass=SERVICE_CLASS,
    paletteCategory=SERVICE_CLASS,
    operatorClass=OPERATOR_CLASS,
    version="0.0.1",
    label="State Trigger",
    description="Triggers exec on `changed` when state `value` changes; ideal for wiring button-like state changes into exec graphs.",
    tags=["execution", "state", "trigger", "event", "button"],
    execInPorts=[],
    execOutPorts=["changed"],
    stateFields=[
        F8StateSpec(
            name="value",
            label="Value",
            description="Watched state value. Exec emits when this changes.",
            valueSchema=any_schema(),
            access=F8StateAccess.rw,
            required=False,
            showOnNode=True,
        ),
        F8StateSpec(
            name="enabled",
            label="Enabled",
            description="Enable/disable trigger emission on value changes.",
            valueSchema=boolean_schema(default=True),
            access=F8StateAccess.rw,
            required=True,
            showOnNode=True,
        ),
        F8StateSpec(
            name="fireOnStart",
            label="Fire On Start",
            description="If enabled and `value` has an initial value, emit one exec when the node entrypoint starts.",
            valueSchema=boolean_schema(default=False),
            access=F8StateAccess.rw,
            required=True,
            showOnNode=True,
        ),
    ],
    editableExecInPorts=False,
    editableExecOutPorts=False,
    editableDataInPorts=False,
    editableDataOutPorts=False,
    editableStateFields=False,
)


def register_operator(registry: RuntimeNodeRegistry | None = None) -> RuntimeNodeRegistry:
    reg = registry or RuntimeNodeRegistry.instance()

    def _factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> OperatorNode:
        return StateTriggerRuntimeNode(node_id=node_id, node=node, initial_state=initial_state)

    reg.register(SERVICE_CLASS, OPERATOR_CLASS, _factory, overwrite=True)
    reg.register_operator_spec(StateTriggerRuntimeNode.SPEC, overwrite=True)
    return reg

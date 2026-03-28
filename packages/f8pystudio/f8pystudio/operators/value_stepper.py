from __future__ import annotations

from typing import Any

from f8pysdk import (
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8StateAccess,
    F8StateSpec,
    integer_schema,
    number_schema,
    string_schema,
)
from f8pysdk.nats_naming import ensure_token
from f8pysdk.runtime_node import OperatorNode
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry

from ..constants import SERVICE_CLASS

OPERATOR_CLASS = "f8.value_stepper"
_STEP_MODE_FIXED = "fixed"
_STEP_MODE_ACCELERATED = "accelerated"
_STEP_MODE_VALUES = [_STEP_MODE_FIXED, _STEP_MODE_ACCELERATED]


class ValueStepperRuntimeNode(OperatorNode):
    """
    Studio-only numeric stepper that converts trigger-style state changes into a
    clamped numeric value.

    This is intended as a small glue operator for ControlPanel/button hotkey
    workflows, without depending on pyengine or expression nodes.
    """

    SPEC = F8OperatorSpec(
        schemaVersion=F8OperatorSchemaVersion.f8operator_1,
        serviceClass=SERVICE_CLASS,
        paletteCategory=SERVICE_CLASS,
        operatorClass=OPERATOR_CLASS,
        version="0.0.1",
        label="ValueStepper",
        description="Studio-only numeric stepper for hotkey-driven increase/decrease control with clamp support.",
        tags=["studio", "state", "stepper", "slider", "hotkey"],
        dataInPorts=[],
        dataOutPorts=[],
        execInPorts=[],
        execOutPorts=[],
        rendererClass="default_op",
        editableStateFields=False,
        stateFields=[
            F8StateSpec(
                name="value",
                label="Value",
                description="Current output value after clamp and trigger processing.",
                valueSchema=number_schema(default=0.0),
                access=F8StateAccess.rw,
                required=True,
                uiControl="slider",
                showOnNode=True,
            ),
            F8StateSpec(
                name="min",
                label="Min",
                description="Lower clamp bound.",
                valueSchema=number_schema(default=0.0),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="max",
                label="Max",
                description="Upper clamp bound.",
                valueSchema=number_schema(default=1.0),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="step",
                label="Step",
                description="Fixed increment/decrement size.",
                valueSchema=number_schema(default=0.01, minimum=0.0),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="increaseTrigger",
                label="Increase",
                description="Increment trigger input, typically driven by a button state edge.",
                valueSchema=integer_schema(default=0),
                access=F8StateAccess.rw,
                required=True,
                uiControl="button",
                showOnNode=False,
            ),
            F8StateSpec(
                name="decreaseTrigger",
                label="Decrease",
                description="Decrement trigger input, typically driven by a button state edge.",
                valueSchema=integer_schema(default=0),
                access=F8StateAccess.rw,
                required=True,
                uiControl="button",
                showOnNode=False,
            ),
            F8StateSpec(
                name="stepMode",
                label="Step Mode",
                description="Whether trigger presses use the fixed or accelerated step size.",
                valueSchema=string_schema(default=_STEP_MODE_FIXED, enum=list(_STEP_MODE_VALUES)),
                access=F8StateAccess.rw,
                required=True,
                uiControl="select",
                showOnNode=False,
            ),
            F8StateSpec(
                name="acceleratedStep",
                label="Accelerated Step",
                description="Larger step size used when stepMode is accelerated.",
                valueSchema=number_schema(default=0.05, minimum=0.0),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="lastTriggerTsMs",
                label="Last Trigger Ts",
                description="Last trigger timestamp in milliseconds for debug/inspection.",
                valueSchema=integer_schema(default=0),
                access=F8StateAccess.ro,
                required=True,
                showOnNode=False,
            ),
        ],
    )

    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        initial = dict(initial_state or {})
        minimum = self._coerce_float(initial.get("min"), 0.0)
        maximum = self._coerce_float(initial.get("max"), 1.0)
        self._minimum, self._maximum = self._ordered_bounds(minimum, maximum)
        self._step = self._coerce_non_negative_float(initial.get("step"), 0.01)
        self._accelerated_step = self._coerce_non_negative_float(initial.get("acceleratedStep"), 0.05)
        self._step_mode = self._coerce_step_mode(initial.get("stepMode"))
        self._value = self._clamp_value(self._coerce_float(initial.get("value"), 0.0))
        self._increase_trigger = self._coerce_int(initial.get("increaseTrigger"), 0)
        self._decrease_trigger = self._coerce_int(initial.get("decreaseTrigger"), 0)
        self._last_trigger_ts_ms = self._coerce_int(initial.get("lastTriggerTsMs"), 0)

        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[p.name for p in (node.dataInPorts or [])],
            data_out_ports=[p.name for p in (node.dataOutPorts or [])],
            state_fields=[s.name for s in (node.stateFields or [])],
            exec_in_ports=list(node.execInPorts or []),
            exec_out_ports=list(node.execOutPorts or []),
        )

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        if value is None:
            return float(default)
        return float(value)

    @staticmethod
    def _coerce_non_negative_float(value: Any, default: float) -> float:
        coerced = ValueStepperRuntimeNode._coerce_float(value, default)
        return coerced if coerced >= 0.0 else 0.0

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        if value is None:
            return int(default)
        return int(value)

    @staticmethod
    def _ordered_bounds(minimum: float, maximum: float) -> tuple[float, float]:
        if minimum <= maximum:
            return minimum, maximum
        return maximum, minimum

    @staticmethod
    def _coerce_step_mode(value: Any) -> str:
        text = str(value or _STEP_MODE_FIXED).strip().lower()
        if text not in _STEP_MODE_VALUES:
            return _STEP_MODE_FIXED
        return text

    def _clamp_value(self, value: float) -> float:
        lower, upper = self._ordered_bounds(self._minimum, self._maximum)
        if value < lower:
            return lower
        if value > upper:
            return upper
        return value

    def _effective_step(self) -> float:
        if self._step_mode == _STEP_MODE_ACCELERATED:
            return self._accelerated_step
        return self._step

    async def validate_state(self, field: str, value: Any, *, ts_ms: int, meta: dict[str, Any]) -> Any:
        del ts_ms
        del meta

        if field == "value":
            return self._clamp_value(self._coerce_float(value, self._value))
        if field == "min":
            candidate = self._coerce_float(value, self._minimum)
            return candidate if candidate <= self._maximum else self._maximum
        if field == "max":
            candidate = self._coerce_float(value, self._maximum)
            return candidate if candidate >= self._minimum else self._minimum
        if field == "step":
            return self._coerce_non_negative_float(value, self._step)
        if field == "acceleratedStep":
            return self._coerce_non_negative_float(value, self._accelerated_step)
        if field == "increaseTrigger":
            return self._coerce_int(value, self._increase_trigger)
        if field == "decreaseTrigger":
            return self._coerce_int(value, self._decrease_trigger)
        if field == "stepMode":
            return self._coerce_step_mode(value)
        if field == "lastTriggerTsMs":
            return self._coerce_int(value, self._last_trigger_ts_ms)
        return value

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        if field == "value":
            self._value = self._clamp_value(self._coerce_float(value, self._value))
            return

        if field == "min":
            self._minimum = self._coerce_float(value, self._minimum)
            await self._sync_value_after_bounds_change(ts_ms=ts_ms)
            return

        if field == "max":
            self._maximum = self._coerce_float(value, self._maximum)
            await self._sync_value_after_bounds_change(ts_ms=ts_ms)
            return

        if field == "step":
            self._step = self._coerce_non_negative_float(value, self._step)
            return

        if field == "acceleratedStep":
            self._accelerated_step = self._coerce_non_negative_float(value, self._accelerated_step)
            return

        if field == "stepMode":
            self._step_mode = self._coerce_step_mode(value)
            return

        if field == "increaseTrigger":
            self._increase_trigger = self._coerce_int(value, self._increase_trigger)
            await self._apply_trigger(delta=1.0, ts_ms=ts_ms)
            return

        if field == "decreaseTrigger":
            self._decrease_trigger = self._coerce_int(value, self._decrease_trigger)
            await self._apply_trigger(delta=-1.0, ts_ms=ts_ms)
            return

        if field == "lastTriggerTsMs":
            self._last_trigger_ts_ms = self._coerce_int(value, self._last_trigger_ts_ms)

    async def _sync_value_after_bounds_change(self, *, ts_ms: int | None) -> None:
        self._minimum, self._maximum = self._ordered_bounds(self._minimum, self._maximum)
        clamped_value = self._clamp_value(self._value)
        if clamped_value == self._value:
            return
        self._value = clamped_value
        await self.set_state("value", clamped_value, ts_ms=ts_ms)

    async def _apply_trigger(self, *, delta: float, ts_ms: int | None) -> None:
        next_value = self._clamp_value(self._value + (delta * self._effective_step()))
        self._value = next_value
        await self.set_state("value", next_value, ts_ms=ts_ms)
        trigger_ts = 0 if ts_ms is None else int(ts_ms)
        self._last_trigger_ts_ms = trigger_ts
        await self.set_state("lastTriggerTsMs", trigger_ts, ts_ms=ts_ms)


def register_operator(registry: RuntimeNodeRegistry | None = None) -> RuntimeNodeRegistry:
    reg = registry or RuntimeNodeRegistry.instance()

    def _factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> OperatorNode:
        return ValueStepperRuntimeNode(node_id=node_id, node=node, initial_state=initial_state)

    reg.register(SERVICE_CLASS, OPERATOR_CLASS, _factory, overwrite=True)
    reg.register_operator_spec(ValueStepperRuntimeNode.SPEC, overwrite=True)
    return reg

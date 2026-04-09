from __future__ import annotations

import unittest

from f8pysdk.specs import (
    F8Edge,
    F8EdgeKindEnum,
    F8EdgeStrategyEnum,
    F8RuntimeGraph,
    F8RuntimeNode,
    F8StateAccess,
    F8StateSpec,
)
from f8pysdk.nodes import RuntimeNode
from f8pysdk.registry import create_runtime_node_registry
from f8pysdk.specs import integer_schema, number_schema
from f8pysdk.specs import (
    can_add as policy_can_add,
    can_delete as policy_can_delete,
    can_edit_existing as policy_can_edit_existing,
)
from f8pysdk.testing import ServiceBusHarness

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS
from f8pystudio.operators.value_stepper import OPERATOR_CLASS, ValueStepperRuntimeNode, register_operator


def _operator_spec_node(
    *,
    node_id: str,
    operator_class: str,
    state_fields: list[F8StateSpec],
) -> F8RuntimeNode:
    return F8RuntimeNode(
        nodeId=node_id,
        serviceId="svc_studio",
        serviceClass=SERVICE_CLASS,
        operatorClass=operator_class,
        stateFields=state_fields,
        stateValues={},
    )


def _value_stepper_node(node_id: str = "stepper") -> F8RuntimeNode:
    return _operator_spec_node(
        node_id=node_id,
        operator_class=OPERATOR_CLASS,
        state_fields=list(ValueStepperRuntimeNode.SPEC.stateFields or []),
    )


class ValueStepperOperatorSpecTests(unittest.TestCase):
    def test_register_operator_exposes_value_stepper_spec(self) -> None:
        registry = create_runtime_node_registry()
        register_operator(registry)

        spec = next(
            operator_spec
            for operator_spec in registry.operator_specs(SERVICE_CLASS)
            if operator_spec.operatorClass == OPERATOR_CLASS
        )
        fields = {state_field.name: state_field for state_field in list(spec.stateFields or [])}

        self.assertEqual(spec.label, "Value Stepper")
        self.assertFalse(policy_can_add(spec, "stateFields"))
        self.assertFalse(policy_can_delete(spec, "stateFields"))
        self.assertTrue(policy_can_edit_existing(spec, "stateFields"))
        self.assertEqual(fields["value"].uiControl, "slider")
        self.assertTrue(bool(fields["value"].showOnNode))
        self.assertEqual(fields["increaseTrigger"].uiControl, "button")
        self.assertEqual(fields["decreaseTrigger"].uiControl, "button")
        self.assertEqual(fields["loop"].uiControl, "toggle")
        self.assertEqual(fields["stepMode"].uiControl, "select")
        self.assertEqual(list(fields["stepMode"].valueSchema.enum or []), ["fixed", "accelerated", "adaptive"])
        self.assertNotIn("lastTriggerTsMs", fields)


class ValueStepperRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.harness = ServiceBusHarness()
        self.bus = self.harness.create_bus("svc_studio")

    async def _assert_state_value_close(self, node_id: str, field: str, expected: float) -> None:
        value = (await self.bus.get_state(node_id, field)).value
        self.assertAlmostEqual(float(value), expected)

    async def test_increase_trigger_uses_fixed_step(self) -> None:
        stepper_node = _value_stepper_node()
        runtime = ValueStepperRuntimeNode(
            node_id="stepper",
            node=stepper_node,
            initial_state={"value": 0.4, "min": 0.0, "max": 1.0, "step": 0.1},
        )
        self.bus.register_node(runtime)

        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[stepper_node], edges=[])
        await self.bus.set_rungraph(graph)

        await self.bus.publish_state_runtime("stepper", "increaseTrigger", 1, ts_ms=123)

        await self._assert_state_value_close("stepper", "value", 0.5)

    async def test_decrease_trigger_clamps_at_minimum(self) -> None:
        stepper_node = _value_stepper_node()
        runtime = ValueStepperRuntimeNode(
            node_id="stepper",
            node=stepper_node,
            initial_state={"value": 0.05, "min": 0.0, "max": 1.0, "step": 0.1},
        )
        self.bus.register_node(runtime)

        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[stepper_node], edges=[])
        await self.bus.set_rungraph(graph)

        await self.bus.publish_state_runtime("stepper", "decreaseTrigger", 1, ts_ms=124)

        await self._assert_state_value_close("stepper", "value", 0.0)

    async def test_increase_trigger_wraps_when_loop_is_enabled(self) -> None:
        stepper_node = _value_stepper_node()
        runtime = ValueStepperRuntimeNode(
            node_id="stepper",
            node=stepper_node,
            initial_state={"value": 0.95, "min": 0.0, "max": 1.0, "step": 0.1, "loop": True},
        )
        self.bus.register_node(runtime)

        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[stepper_node], edges=[])
        await self.bus.set_rungraph(graph)

        await self.bus.publish_state_runtime("stepper", "increaseTrigger", 1, ts_ms=124)

        await self._assert_state_value_close("stepper", "value", 0.05)

    async def test_manual_value_write_wraps_when_loop_is_enabled(self) -> None:
        stepper_node = _value_stepper_node()
        runtime = ValueStepperRuntimeNode(
            node_id="stepper",
            node=stepper_node,
            initial_state={"value": 0.25, "min": 0.0, "max": 1.0, "loop": True},
        )
        self.bus.register_node(runtime)

        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[stepper_node], edges=[])
        await self.bus.set_rungraph(graph)

        await self.bus.publish_state_runtime("stepper", "value", 1.2, ts_ms=126)

        await self._assert_state_value_close("stepper", "value", 0.2)

    async def test_accelerated_mode_uses_accelerated_step(self) -> None:
        stepper_node = _value_stepper_node()
        runtime = ValueStepperRuntimeNode(
            node_id="stepper",
            node=stepper_node,
            initial_state={
                "value": 0.2,
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "stepMode": "accelerated",
                "acceleratedStep": 0.25,
            },
        )
        self.bus.register_node(runtime)

        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[stepper_node], edges=[])
        await self.bus.set_rungraph(graph)

        await self.bus.publish_state_runtime("stepper", "increaseTrigger", 1, ts_ms=125)

        await self._assert_state_value_close("stepper", "value", 0.45)

    async def test_adaptive_mode_uses_fixed_step_for_first_trigger(self) -> None:
        stepper_node = _value_stepper_node()
        runtime = ValueStepperRuntimeNode(
            node_id="stepper",
            node=stepper_node,
            initial_state={
                "value": 0.2,
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "stepMode": "adaptive",
                "acceleratedStep": 0.25,
            },
        )
        self.bus.register_node(runtime)

        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[stepper_node], edges=[])
        await self.bus.set_rungraph(graph)

        await self.bus.publish_state_runtime("stepper", "increaseTrigger", 1, ts_ms=1000)

        await self._assert_state_value_close("stepper", "value", 0.21)

    async def test_adaptive_mode_uses_accelerated_step_for_rapid_repeat_trigger(self) -> None:
        stepper_node = _value_stepper_node()
        runtime = ValueStepperRuntimeNode(
            node_id="stepper",
            node=stepper_node,
            initial_state={
                "value": 0.2,
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "stepMode": "adaptive",
                "acceleratedStep": 0.25,
            },
        )
        self.bus.register_node(runtime)

        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[stepper_node], edges=[])
        await self.bus.set_rungraph(graph)

        await self.bus.publish_state_runtime("stepper", "increaseTrigger", 1, ts_ms=1000)
        await self.bus.publish_state_runtime("stepper", "increaseTrigger", 2, ts_ms=1100)

        await self._assert_state_value_close("stepper", "value", 0.46)

    async def test_adaptive_mode_falls_back_to_fixed_step_after_pause(self) -> None:
        stepper_node = _value_stepper_node()
        runtime = ValueStepperRuntimeNode(
            node_id="stepper",
            node=stepper_node,
            initial_state={
                "value": 0.2,
                "min": 0.0,
                "max": 1.0,
                "step": 0.01,
                "stepMode": "adaptive",
                "acceleratedStep": 0.25,
            },
        )
        self.bus.register_node(runtime)

        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[stepper_node], edges=[])
        await self.bus.set_rungraph(graph)

        await self.bus.publish_state_runtime("stepper", "increaseTrigger", 1, ts_ms=1000)
        await self.bus.publish_state_runtime("stepper", "increaseTrigger", 2, ts_ms=1100)
        await self.bus.publish_state_runtime("stepper", "increaseTrigger", 3, ts_ms=1500)

        await self._assert_state_value_close("stepper", "value", 0.47)

    async def test_manual_value_write_is_clamped_by_validate_state(self) -> None:
        stepper_node = _value_stepper_node()
        runtime = ValueStepperRuntimeNode(
            node_id="stepper",
            node=stepper_node,
            initial_state={"value": 0.25, "min": 0.0, "max": 1.0},
        )
        self.bus.register_node(runtime)

        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[stepper_node], edges=[])
        await self.bus.set_rungraph(graph)

        await self.bus.publish_state_runtime("stepper", "value", 2.0, ts_ms=126)

        await self._assert_state_value_close("stepper", "value", 1.0)

    async def test_bounds_change_reclamps_current_value(self) -> None:
        stepper_node = _value_stepper_node()
        runtime = ValueStepperRuntimeNode(
            node_id="stepper",
            node=stepper_node,
            initial_state={"value": 0.8, "min": 0.0, "max": 1.0},
        )
        self.bus.register_node(runtime)

        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[stepper_node], edges=[])
        await self.bus.set_rungraph(graph)

        await self.bus.publish_state_runtime("stepper", "max", 0.3, ts_ms=127)

        await self._assert_state_value_close("stepper", "max", 0.3)
        await self._assert_state_value_close("stepper", "value", 0.3)

    async def test_state_edges_drive_stepper_and_forward_to_numeric_target(self) -> None:
        increase_node = _operator_spec_node(
            node_id="inc",
            operator_class="test.button_increase",
            state_fields=[
                F8StateSpec(
                    name="value",
                    valueSchema=integer_schema(default=0),
                    access=F8StateAccess.rw,
                    uiControl="button",
                )
            ],
        )
        decrease_node = _operator_spec_node(
            node_id="dec",
            operator_class="test.button_decrease",
            state_fields=[
                F8StateSpec(
                    name="value",
                    valueSchema=integer_schema(default=0),
                    access=F8StateAccess.rw,
                    uiControl="button",
                )
            ],
        )
        stepper_node = _value_stepper_node()
        sink_node = _operator_spec_node(
            node_id="sink",
            operator_class="test.slider_sink",
            state_fields=[
                F8StateSpec(
                    name="value",
                    valueSchema=number_schema(default=0.0),
                    access=F8StateAccess.rw,
                    uiControl="slider",
                )
            ],
        )

        self.bus.register_node(RuntimeNode(node_id="inc"))
        self.bus.register_node(RuntimeNode(node_id="dec"))
        self.bus.register_node(
            ValueStepperRuntimeNode(
                node_id="stepper",
                node=stepper_node,
                initial_state={"value": 0.5, "min": 0.0, "max": 1.0, "step": 0.2},
            )
        )
        self.bus.register_node(RuntimeNode(node_id="sink"))

        graph = F8RuntimeGraph(
            graphId="g1",
            revision="r1",
            nodes=[increase_node, decrease_node, stepper_node, sink_node],
            edges=[
                F8Edge(
                    edgeId="e-inc",
                    fromServiceId="svc_studio",
                    fromOperatorId="inc",
                    fromPort="value",
                    toServiceId="svc_studio",
                    toOperatorId="stepper",
                    toPort="increaseTrigger",
                    kind=F8EdgeKindEnum.state,
                    strategy=F8EdgeStrategyEnum.latest,
                ),
                F8Edge(
                    edgeId="e-dec",
                    fromServiceId="svc_studio",
                    fromOperatorId="dec",
                    fromPort="value",
                    toServiceId="svc_studio",
                    toOperatorId="stepper",
                    toPort="decreaseTrigger",
                    kind=F8EdgeKindEnum.state,
                    strategy=F8EdgeStrategyEnum.latest,
                ),
                F8Edge(
                    edgeId="e-out",
                    fromServiceId="svc_studio",
                    fromOperatorId="stepper",
                    fromPort="value",
                    toServiceId="svc_studio",
                    toOperatorId="sink",
                    toPort="value",
                    kind=F8EdgeKindEnum.state,
                    strategy=F8EdgeStrategyEnum.latest,
                ),
            ],
        )
        await self.bus.set_rungraph(graph)

        await self.bus.publish_state_runtime("inc", "value", 1, ts_ms=200)
        await self._assert_state_value_close("stepper", "value", 0.7)
        await self._assert_state_value_close("sink", "value", 0.7)

        await self.bus.publish_state_runtime("dec", "value", 1, ts_ms=201)
        await self._assert_state_value_close("stepper", "value", 0.5)
        await self._assert_state_value_close("sink", "value", 0.5)


if __name__ == "__main__":
    unittest.main()

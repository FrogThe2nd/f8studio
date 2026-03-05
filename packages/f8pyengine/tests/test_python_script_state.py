from f8pysdk.msgspec_codec import dump_json
import asyncio
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SDK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SDK_ROOT not in sys.path:
    sys.path.insert(0, SDK_ROOT)

from f8pysdk import F8StateAccess, F8StateSpec, any_schema  # noqa: E402
from f8pysdk.generated import F8RuntimeGraph, F8RuntimeNode  # noqa: E402
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry  # noqa: E402
from f8pysdk.service_host import ServiceHost, ServiceHostConfig  # noqa: E402
from f8pysdk.testing import ServiceBusHarness  # noqa: E402

from f8pyengine.constants import SERVICE_CLASS  # noqa: E402
from f8pyengine.operators.python_script import PythonScriptRuntimeNode, register_operator  # noqa: E402


def _runtime_python_script_node(
    *,
    node_id: str,
    code: str,
    state_fields: list[F8StateSpec] | None = None,
    state_values: dict[str, object] | None = None,
) -> F8RuntimeNode:
    spec = PythonScriptRuntimeNode.SPEC
    merged_state_values: dict[str, object] = {"code": code}
    if state_values is not None:
        merged_state_values.update(state_values)
    return F8RuntimeNode(
        nodeId=node_id,
        serviceId="svcA",
        serviceClass=SERVICE_CLASS,
        operatorClass=spec.operatorClass,
        execInPorts=list(spec.execInPorts or []),
        execOutPorts=list(spec.execOutPorts or []),
        dataInPorts=list(spec.dataInPorts or []),
        dataOutPorts=list(spec.dataOutPorts or []),
        stateFields=list(state_fields if state_fields is not None else (spec.stateFields or [])),
        stateValues=merged_state_values,
    )


class PythonScriptStateTests(unittest.IsolatedAsyncioTestCase):
    def test_spec_contains_editor_assist_protocol(self) -> None:
        spec = PythonScriptRuntimeNode.SPEC
        code_field = next((f for f in list(spec.stateFields or []) if str(f.name or "").strip() == "code"), None)
        self.assertIsNotNone(code_field)
        assert code_field is not None
        editor_assist = code_field.editorAssist
        self.assertIsNotNone(editor_assist)
        python_payload = dump_json(editor_assist.python, mode="json") if editor_assist is not None else None
        self.assertIsInstance(python_payload, dict)
        support_files = (python_payload or {}).get("support_files") if isinstance(python_payload, dict) else None
        self.assertIsInstance(support_files, dict)
        api_stub = str((support_files or {}).get("f8_script_api.pyi") or "")
        self.assertIn("from f8_dynamic_inputs import F8Inputs as F8Inputs", api_stub)
        dynamic_bindings = (python_payload or {}).get("dynamic_bindings") if isinstance(python_payload, dict) else None
        self.assertIsInstance(dynamic_bindings, dict)
        inputs_binding = (dynamic_bindings or {}).get("inputs") if isinstance(dynamic_bindings, dict) else None
        self.assertIsInstance(inputs_binding, dict)
        self.assertTrue(bool((inputs_binding or {}).get("enabled")))
        states_binding = (dynamic_bindings or {}).get("states") if isinstance(dynamic_bindings, dict) else None
        self.assertIsInstance(states_binding, dict)
        self.assertTrue(bool((states_binding or {}).get("enabled")))

    async def test_on_state_runs_and_can_write_state(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_operator(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        code = (
            "def onState(ctx, field, value, tsMs=None):\n"
            "    if field == 'foo':\n"
            "        ctx.set_state('bar', int(value) * 2)\n"
        )

        state_fields = list(PythonScriptRuntimeNode.SPEC.stateFields or [])
        state_fields.append(F8StateSpec(name="foo", label="foo", description="", valueSchema=any_schema(), access=F8StateAccess.rw))
        state_fields.append(F8StateSpec(name="bar", label="bar", description="", valueSchema=any_schema(), access=F8StateAccess.ro))

        op = _runtime_python_script_node(node_id="ps1", code=code, state_fields=state_fields)
        graph = F8RuntimeGraph(graphId="g1", revision="r1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)

        await bus.publish_state_external("ps1", "foo", 21, source="test")
        await asyncio.sleep(0.05)
        node = bus.get_node("ps1")
        self.assertIsInstance(node, PythonScriptRuntimeNode)
        assert isinstance(node, PythonScriptRuntimeNode)
        bar = await node.get_state_value("bar")
        self.assertEqual(int(bar), 42)

    async def test_python_script_exec_enabled_by_default(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_operator(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        state_fields = list(PythonScriptRuntimeNode.SPEC.stateFields or [])
        state_fields.append(
            F8StateSpec(name="booted", label="booted", description="", valueSchema=any_schema(), access=F8StateAccess.ro)
        )
        op = _runtime_python_script_node(
            node_id="ps2",
            code="def onStart(ctx):\n    ctx.set_state('booted', True)\n",
            state_fields=state_fields,
        )
        graph = F8RuntimeGraph(graphId="g2", revision="r1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)
        await asyncio.sleep(0.05)

        node = bus.get_node("ps2")
        self.assertIsInstance(node, PythonScriptRuntimeNode)
        assert isinstance(node, PythonScriptRuntimeNode)
        booted = await node.get_state_value("booted")
        self.assertTrue(bool(booted))

    async def test_compute_output_uses_on_exec_outputs_in_pull_mode(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_operator(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        code = (
            "def onExec(ctx, exec_in, inputs):\n"
            "    return {'outputs': {'out': 123}}\n"
        )
        op = _runtime_python_script_node(node_id="ps3", code=code)
        graph = F8RuntimeGraph(graphId="g3", revision="r1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)

        node = bus.get_node("ps3")
        self.assertIsInstance(node, PythonScriptRuntimeNode)
        assert isinstance(node, PythonScriptRuntimeNode)

        out = await node.compute_output("out", ctx_id="ctx-1")
        self.assertEqual(out, 123)

    async def test_compute_output_falls_back_to_on_msg_when_on_exec_missing(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_operator(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        code = (
            "def onMsg(ctx, inputs):\n"
            "    return 99\n"
        )
        op = _runtime_python_script_node(node_id="ps4", code=code)
        graph = F8RuntimeGraph(graphId="g4", revision="r1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)

        node = bus.get_node("ps4")
        self.assertIsInstance(node, PythonScriptRuntimeNode)
        assert isinstance(node, PythonScriptRuntimeNode)

        out = await node.compute_output("out", ctx_id="ctx-2")
        self.assertEqual(out, 99)

    async def test_ctx_locals_persists_between_calls(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_operator(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        code = (
            "def onMsg(ctx, inputs):\n"
            "    c = int(ctx.locals.get('count') or 0)\n"
            "    c += 1\n"
            "    ctx.locals['count'] = c\n"
            "    return {'outputs': {'out': c}}\n"
        )
        op = _runtime_python_script_node(node_id="ps5", code=code)
        graph = F8RuntimeGraph(graphId="g5", revision="r1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)

        node = bus.get_node("ps5")
        self.assertIsInstance(node, PythonScriptRuntimeNode)
        assert isinstance(node, PythonScriptRuntimeNode)

        out1 = await node.compute_output("out", ctx_id="ctx-5a")
        out2 = await node.compute_output("out", ctx_id="ctx-5b")
        self.assertEqual(out1, 1)
        self.assertEqual(out2, 2)

    async def test_ctx_locals_is_isolated_from_system_state(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_operator(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        state_fields = list(PythonScriptRuntimeNode.SPEC.stateFields or [])
        state_fields.append(F8StateSpec(name="x", label="x", description="", valueSchema=any_schema(), access=F8StateAccess.rw))
        code = (
            "async def onExec(ctx, exec_in, inputs):\n"
            "    ctx.locals['x'] = 1\n"
            "    await ctx.set_state_async('x', 2)\n"
            "    v = await ctx.read_state('x')\n"
            "    return {'outputs': {'out': v}}\n"
        )
        op = _runtime_python_script_node(node_id="ps6", code=code, state_fields=state_fields)
        graph = F8RuntimeGraph(graphId="g6", revision="r1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)

        node = bus.get_node("ps6")
        self.assertIsInstance(node, PythonScriptRuntimeNode)
        assert isinstance(node, PythonScriptRuntimeNode)

        out = await node.compute_output("out", ctx_id="ctx-6a")
        self.assertEqual(out, 2)
        state_x = await node.get_state_value("x")
        self.assertEqual(state_x, 2)
        self.assertEqual(node._locals.get("x"), 1)

    async def test_get_state_cached_sync_snapshot(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_operator(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        state_fields = list(PythonScriptRuntimeNode.SPEC.stateFields or [])
        state_fields.append(F8StateSpec(name="x", label="x", description="", valueSchema=any_schema(), access=F8StateAccess.rw))
        code = (
            "def onExec(ctx, exec_in, inputs):\n"
            "    v = ctx.states.get('x')\n"
            "    if v is None:\n"
            "        v = 7\n"
            "    return {'outputs': {'out': v}}\n"
        )
        op = _runtime_python_script_node(node_id="ps7", code=code, state_fields=state_fields)
        graph = F8RuntimeGraph(graphId="g7", revision="r1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)

        node = bus.get_node("ps7")
        self.assertIsInstance(node, PythonScriptRuntimeNode)
        assert isinstance(node, PythonScriptRuntimeNode)

        out1 = await node.compute_output("out", ctx_id="ctx-7a")
        self.assertEqual(out1, 7)

        await bus.publish_state_external("ps7", "x", 33, source="test")
        await asyncio.sleep(0.05)
        out2 = await node.compute_output("out", ctx_id="ctx-7b")
        self.assertEqual(out2, 33)

    async def test_states_view_supports_object_and_mapping_access_and_hides_wo(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_operator(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        state_fields = list(PythonScriptRuntimeNode.SPEC.stateFields or [])
        state_fields.append(
            F8StateSpec(name="rw_state", label="rw_state", description="", valueSchema=any_schema(), access=F8StateAccess.rw)
        )
        state_fields.append(
            F8StateSpec(name="ro_state", label="ro_state", description="", valueSchema=any_schema(), access=F8StateAccess.ro)
        )
        state_fields.append(
            F8StateSpec(name="wo_state", label="wo_state", description="", valueSchema=any_schema(), access=F8StateAccess.wo)
        )
        code = (
            "def onStart(ctx):\n"
            "    ctx.set_state('ro_state', 8)\n"
            "\n"
            "def onExec(ctx, exec_in, inputs):\n"
            "    dot_v = ctx.states.rw_state\n"
            "    map_v = ctx.states['ro_state']\n"
            "    get_v = ctx.states.get('ro_state')\n"
            "    has_wo = 'wo_state' in ctx.states\n"
            "    return {'outputs': {'out': [dot_v, map_v, get_v, has_wo]}}\n"
        )
        op = _runtime_python_script_node(node_id="ps11", code=code, state_fields=state_fields)
        graph = F8RuntimeGraph(graphId="g11", revision="r1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)

        await asyncio.sleep(0.05)
        await bus.publish_state_external("ps11", "rw_state", 7, source="test")
        await asyncio.sleep(0.05)

        node = bus.get_node("ps11")
        self.assertIsInstance(node, PythonScriptRuntimeNode)
        assert isinstance(node, PythonScriptRuntimeNode)
        out = await node.compute_output("out", ctx_id="ctx-11")
        self.assertEqual(out, [7, 8, 8, False])

    async def test_inputs_supports_object_and_mapping_access(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_operator(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        code = (
            "def onMsg(ctx, inputs):\n"
            "    dot_v = inputs.msg if 'msg' in inputs else None\n"
            "    idx_v = inputs['msg'] if 'msg' in inputs else None\n"
            "    get_v = inputs.get('msg')\n"
            "    return {'outputs': {'out': [dot_v, idx_v, get_v]}}\n"
        )
        op = _runtime_python_script_node(node_id="ps9", code=code)
        graph = F8RuntimeGraph(graphId="g9", revision="r1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)

        node = bus.get_node("ps9")
        self.assertIsInstance(node, PythonScriptRuntimeNode)
        assert isinstance(node, PythonScriptRuntimeNode)
        out = await node._compute_outputs_for_pull({"msg": 123}, exec_in=None)
        self.assertEqual(out.get("out"), [123, 123, 123])

    async def test_inputs_nested_object_supports_dot_access(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_operator(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        code = (
            "def onMsg(ctx, inputs):\n"
            "    val_dot = inputs.payload.user.name\n"
            "    val_map = inputs['payload']['user']['name']\n"
            "    val_get = inputs.get('payload').get('user').get('name')\n"
            "    return {'outputs': {'out': [val_dot, val_map, val_get]}}\n"
        )
        op = _runtime_python_script_node(node_id="ps10", code=code)
        graph = F8RuntimeGraph(graphId="g10", revision="r1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)

        node = bus.get_node("ps10")
        self.assertIsInstance(node, PythonScriptRuntimeNode)
        assert isinstance(node, PythonScriptRuntimeNode)
        out = await node._compute_outputs_for_pull({"payload": {"user": {"name": "alice"}}}, exec_in=None)
        self.assertEqual(out.get("out"), ["alice", "alice", "alice"])

    async def test_legacy_ctx_dict_access_sets_last_error(self) -> None:
        harness = ServiceBusHarness()
        bus = harness.create_bus("svcA")
        reg = RuntimeNodeRegistry.instance()
        register_operator(reg)
        _ = ServiceHost(bus, config=ServiceHostConfig(service_class=SERVICE_CLASS), registry=reg)

        code = (
            "def onStart(ctx):\n"
            "    ctx['log']('legacy syntax')\n"
        )
        op = _runtime_python_script_node(node_id="ps8", code=code)
        graph = F8RuntimeGraph(graphId="g8", revision="r1", nodes=[op], edges=[])
        await bus.set_rungraph(graph)
        await asyncio.sleep(0.05)

        node = bus.get_node("ps8")
        self.assertIsInstance(node, PythonScriptRuntimeNode)
        assert isinstance(node, PythonScriptRuntimeNode)
        last_error = await node.get_state_value("lastError")
        error_text = str(last_error or node._last_error or "")
        self.assertIn("not subscriptable", error_text)

if __name__ == "__main__":
    unittest.main()

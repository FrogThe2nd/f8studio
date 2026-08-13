from __future__ import annotations

from f8pyengine.constants import SERVICE_CLASS as PYENGINE_SERVICE_CLASS
from f8pyengine.pyengine_node_registry import create_pyengine_registry
from f8pysdk.registry import Registry
from f8pysdk.specs import F8OperatorSpec
from f8pystudio.agents.graph_builder import decode_graph_build_plan
from f8pystudio.modding.graph_templates import skeleton_osr_graph_build_plan, skeleton_stream_graph_build_plan
from f8pystudio.operators.viz_three_d import register_operator as register_viz_three_d
from f8pystudio.studio_specs.identifiers import SERVICE_CLASS as STUDIO_SERVICE_CLASS
from f8pystudio_ext_viz_tcode.operators.viz_tcode import register_operator as register_viz_tcode


def test_stream_preview_uses_registered_viz_operator_name() -> None:
    plan = skeleton_stream_graph_build_plan(port=39540)

    assert [node["nodeType"] for node in plan["nodes"]] == [
        "f8.udp_in",
        "f8.skeleton_decoder",
        "f8.viz.three_d",
    ]
    decode_graph_build_plan(plan)


def test_osr_plan_has_valid_operator_ports_and_state_fields() -> None:
    plan = skeleton_osr_graph_build_plan(profile_id="hs2")
    decode_graph_build_plan(plan)
    specs = _operator_specs()
    nodes_by_id = {str(node["nodeId"]): node for node in plan["nodes"]}

    for node in plan["nodes"]:
        node_type = str(node["nodeType"])
        spec = specs[node_type]
        state_names = {str(state.name) for state in list(spec.stateFields or [])}
        assert set(dict(node["stateValues"])) <= state_names

    for connection in plan["connections"]:
        source = specs[str(nodes_by_id[str(connection["fromNodeId"])]["nodeType"])]
        target = specs[str(nodes_by_id[str(connection["toNodeId"])]["nodeType"])]
        source_ports = {str(port.name) for port in list(source.dataOutPorts or [])}
        source_ports.update(str(port) for port in list(source.execOutPorts or []))
        target_ports = {str(port.name) for port in list(target.dataInPorts or [])}
        target_ports.update(str(port) for port in list(target.execInPorts or []))
        assert str(connection["fromPort"]) in source_ports
        assert str(connection["toPort"]) in target_ports


def test_osr_plan_requires_stable_identity_and_disarms_physical_output() -> None:
    plan = skeleton_osr_graph_build_plan(profile_id="hs2")
    nodes = {str(node["nodeId"]): node for node in plan["nodes"]}

    assert nodes["modding_reference_selector"]["stateValues"] == {
        "profileId": "hs2",
        "role": "male",
        "roleIndex": 0,
        "allowLegacyFallback": False,
    }
    assert nodes["modding_serial_out"]["stateValues"]["enabled"] is False
    assert plan["safety"] == {
        "requiresVerifiedBinaryStream": True,
        "watchdogTimeoutMs": 250,
        "physicalOutputArmed": False,
        "physicalOutputNodeId": "modding_serial_out",
        "armStateField": "enabled",
    }


def _operator_specs() -> dict[str, F8OperatorSpec]:
    runtime_registry = create_pyengine_registry()
    registry = Registry.wrap(runtime_registry)
    register_viz_three_d(registry)
    register_viz_tcode(registry)
    specs: list[F8OperatorSpec] = []
    specs.extend(runtime_registry.operator_specs(PYENGINE_SERVICE_CLASS))
    specs.extend(runtime_registry.operator_specs(STUDIO_SERVICE_CLASS))
    return {str(spec.operatorClass): spec for spec in specs}

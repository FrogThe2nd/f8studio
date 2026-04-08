from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from qtpy import QtWidgets

from f8pysdk.specs import (
    F8Command,
    F8DataPortSpec,
    F8EdgeKindEnum,
    F8OperatorSpec,
    F8ServiceSpec,
    F8StateAccess,
    F8StateSpec,
)
from f8pysdk.command_state import command_input_state_field, command_output_state_field
from f8pysdk.msgspec_codec import copy_model
from f8pysdk.schema_helpers import any_schema, number_schema

from f8pystudio.nodegraph.operator_basenode import F8StudioOperatorBaseNode
from f8pystudio.nodegraph.service_basenode import F8StudioServiceBaseNode
from f8pystudio.nodegraph.runtime_compiler import compile_global_runtime_graph
from f8pystudio.operators.patch_hub import PatchHubRuntimeNode
from f8pystudio.nodegraph.service_spec_sync import build_command_port


class _FakePort:
    def __init__(self, name: str, node: "_FakeServiceNode") -> None:
        self._name = name
        self._node = node
        self._connected_ports: list[_FakePort] = []

    def name(self) -> str:
        return self._name

    def node(self) -> "_FakeServiceNode":
        return self._node

    def connected_ports(self) -> list["_FakePort"]:
        return list(self._connected_ports)

    def connect_to(self, other: "_FakePort") -> None:
        if other not in self._connected_ports:
            self._connected_ports.append(other)
        if self not in other._connected_ports:
            other._connected_ports.append(self)


@dataclass(eq=False)
class _FakeServiceNode:
    id: str
    spec: F8ServiceSpec
    _name: str = "Service"
    _inputs: list[_FakePort] = field(default_factory=list)
    _outputs: list[_FakePort] = field(default_factory=list)
    added_inputs: list[str] = field(default_factory=list)
    added_outputs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.model = SimpleNamespace(properties={}, custom_properties={})

    def name(self) -> str:
        return self._name

    def effective_commands(self) -> list[F8Command]:
        return list(self.spec.commands or [])

    def effective_state_fields(self) -> list[F8StateSpec]:
        return list(self.spec.stateFields or [])

    def data_port_show_on_node(self, name: str, *, is_in: bool) -> bool:
        del name, is_in
        return True

    def add_input(self, name: str, **kwargs: Any) -> None:
        del kwargs
        self.added_inputs.append(name)

    def add_output(self, name: str, **kwargs: Any) -> None:
        del kwargs
        self.added_outputs.append(name)

    def input_ports(self) -> list[_FakePort]:
        return list(self._inputs)

    def output_ports(self) -> list[_FakePort]:
        return list(self._outputs)

    def add_input_port(self, name: str) -> _FakePort:
        port = _FakePort(name, self)
        self._inputs.append(port)
        return port

    def add_output_port(self, name: str) -> _FakePort:
        port = _FakePort(name, self)
        self._outputs.append(port)
        return port


@dataclass(eq=False)
class _FakeOperatorNode:
    id: str
    svcId: str
    spec: F8OperatorSpec
    _name: str = "Operator"
    _inputs: list[_FakePort] = field(default_factory=list)
    _outputs: list[_FakePort] = field(default_factory=list)
    added_inputs: list[str] = field(default_factory=list)
    added_outputs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.model = SimpleNamespace(properties={}, custom_properties={})

    def name(self) -> str:
        return self._name

    def effective_commands(self) -> list[F8Command]:
        return list(self.spec.commands or [])

    def effective_state_fields(self) -> list[F8StateSpec]:
        return list(self.spec.stateFields or [])

    def data_port_show_on_node(self, name: str, *, is_in: bool) -> bool:
        del name, is_in
        return True

    def add_input(self, name: str, **kwargs: Any) -> None:
        del kwargs
        self.added_inputs.append(name)

    def add_output(self, name: str, **kwargs: Any) -> None:
        del kwargs
        self.added_outputs.append(name)

    def input_ports(self) -> list[_FakePort]:
        return list(self._inputs)

    def output_ports(self) -> list[_FakePort]:
        return list(self._outputs)

    def add_input_port(self, name: str) -> _FakePort:
        port = _FakePort(name, self)
        self._inputs.append(port)
        return port

    def add_output_port(self, name: str) -> _FakePort:
        port = _FakePort(name, self)
        self._outputs.append(port)
        return port


def _patch_hub_spec(*, data_terminals: list[str] | None = None, state_terminals: list[str] | None = None) -> F8OperatorSpec:
    data_ports = [
        F8DataPortSpec(name=name, valueSchema=any_schema(), required=False, showOnNode=True)
        for name in list(data_terminals or [])
    ]
    state_fields = [
        F8StateSpec(name=name, valueSchema=any_schema(), access=F8StateAccess.rw, required=False, showOnNode=True)
        for name in list(state_terminals or [])
    ]
    return copy_model(
        PatchHubRuntimeNode.SPEC,
        update={
            "dataInPorts": data_ports,
            "dataOutPorts": [copy_model(port, update={}) for port in data_ports],
            "stateFields": state_fields,
        },
    )


def test_build_command_port_adds_in_and_out_ports() -> None:
    node = _FakeServiceNode(
        id="svc_cmd",
        spec=F8ServiceSpec(
            serviceClass="f8.test.service",
            label="Service",
            commands=[
                F8Command(name="run", showOnNode=True, params=[]),
                F8Command(name="stop", showOnNode=True, params=[]),
            ],
        ),
    )

    build_command_port(node)

    assert node.added_inputs == ["[C]run", "[C]stop"]
    assert node.added_outputs == ["run[C]", "stop[C]"]


def test_build_command_port_skips_hidden_commands() -> None:
    node = _FakeServiceNode(
        id="svc_cmd_hidden",
        spec=F8ServiceSpec(
            serviceClass="f8.test.service",
            label="Service",
            commands=[
                F8Command(name="run", showOnNode=False, params=[]),
                F8Command(name="stop", showOnNode=True, params=[]),
            ],
        ),
    )

    build_command_port(node)

    assert node.added_inputs == ["[C]stop"]
    assert node.added_outputs == ["stop[C]"]


def test_build_command_port_adds_operator_command_ports() -> None:
    node = _FakeOperatorNode(
        id="op_cmd",
        svcId="svc_parent",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.operator",
            label="Operator",
            commands=[F8Command(name="run", showOnNode=True, params=[])],
        ),
    )

    build_command_port(node)

    assert node.added_inputs == ["[C]run"]
    assert node.added_outputs == ["run[C]"]


def test_runtime_compiler_maps_command_ports_to_hidden_state_edges() -> None:
    src = _FakeServiceNode(
        id="svc_src",
        spec=F8ServiceSpec(
            serviceClass="f8.test.src",
            label="Source",
            stateFields=[F8StateSpec(name="value", valueSchema=number_schema(), access=F8StateAccess.rw, showOnNode=True)],
        ),
    )
    dst = _FakeServiceNode(
        id="svc_dst",
        spec=F8ServiceSpec(
            serviceClass="f8.test.dst",
            label="Destination",
            commands=[F8Command(name="Run Value", showOnNode=True, params=[])],
            dataOutPorts=[F8DataPortSpec(name="result", valueSchema=any_schema())],
        ),
    )

    src_out = src.add_output_port("value[S]")
    dst_in = dst.add_input_port("[C]Run Value")
    src_out.connect_to(dst_in)

    graph = compile_global_runtime_graph(services=[], operators=[], service_nodes=[src, dst])

    dst_runtime = next(node for node in list(graph.nodes or []) if str(node.nodeId) == "svc_dst")
    state_field_names = {str(field.name or "") for field in list(dst_runtime.stateFields or [])}
    assert command_input_state_field("Run Value") in state_field_names
    assert command_output_state_field("Run Value") in state_field_names

    edge = next(iter(list(graph.edges or [])))
    assert edge.kind == F8EdgeKindEnum.state
    assert str(edge.fromPort) == "value"
    assert str(edge.toPort) == command_input_state_field("Run Value")


def test_runtime_compiler_maps_operator_command_ports_to_hidden_state_edges() -> None:
    src = _FakeOperatorNode(
        id="op_src",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.source",
            label="Source",
            stateFields=[F8StateSpec(name="value", valueSchema=number_schema(), access=F8StateAccess.rw, showOnNode=True)],
        ),
    )
    dst = _FakeOperatorNode(
        id="op_dst",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.dest",
            label="Destination",
            commands=[F8Command(name="Run Value", showOnNode=True, params=[])],
        ),
    )

    src_out = src.add_output_port("value[S]")
    dst_in = dst.add_input_port("[C]Run Value")
    src_out.connect_to(dst_in)

    graph = compile_global_runtime_graph(services=[], operators=[src, dst], service_nodes=[])

    dst_runtime = next(node for node in list(graph.nodes or []) if str(node.nodeId) == "op_dst")
    state_field_names = {str(field.name or "") for field in list(dst_runtime.stateFields or [])}
    assert command_input_state_field("Run Value") in state_field_names
    assert command_output_state_field("Run Value") in state_field_names

    edge = next(iter(list(graph.edges or [])))
    assert edge.kind == F8EdgeKindEnum.state
    assert str(edge.fromPort) == "value"
    assert str(edge.toPort) == command_input_state_field("Run Value")


def test_runtime_compiler_lowers_patch_hub_data_fanout() -> None:
    source = _FakeOperatorNode(
        id="op_source",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.source",
            label="Source",
            dataOutPorts=[F8DataPortSpec(name="out", valueSchema=any_schema(), required=False)],
        ),
    )
    hub = _FakeOperatorNode(
        id="op_hub",
        svcId="svc_alpha",
        spec=_patch_hub_spec(data_terminals=["feed"]),
    )
    sink_a = _FakeOperatorNode(
        id="op_sink_a",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.sink",
            label="Sink A",
            dataInPorts=[F8DataPortSpec(name="in", valueSchema=any_schema(), required=False)],
        ),
    )
    sink_b = _FakeOperatorNode(
        id="op_sink_b",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.sink",
            label="Sink B",
            dataInPorts=[F8DataPortSpec(name="in", valueSchema=any_schema(), required=False)],
        ),
    )

    source.add_output_port("out[D]").connect_to(hub.add_input_port("[D]feed"))
    hub.add_output_port("feed[D]").connect_to(sink_a.add_input_port("[D]in"))
    hub.add_output_port("feed[D]").connect_to(sink_b.add_input_port("[D]in"))

    graph = compile_global_runtime_graph(services=[], operators=[source, hub, sink_a, sink_b], service_nodes=[])

    assert "op_hub" not in {str(node.nodeId) for node in list(graph.nodes or [])}
    lowered_edges = [edge for edge in list(graph.edges or []) if edge.kind == F8EdgeKindEnum.data]
    assert len(lowered_edges) == 2
    assert {
        (str(edge.fromOperatorId or ""), str(edge.fromPort or ""), str(edge.toOperatorId or ""), str(edge.toPort or ""))
        for edge in lowered_edges
    } == {
        ("op_source", "out", "op_sink_a", "in"),
        ("op_source", "out", "op_sink_b", "in"),
    }


def test_runtime_compiler_lowers_patch_hub_state_to_command_input() -> None:
    source = _FakeOperatorNode(
        id="op_source",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.source",
            label="Source",
            stateFields=[F8StateSpec(name="value", valueSchema=number_schema(), access=F8StateAccess.rw, showOnNode=True)],
        ),
    )
    hub = _FakeOperatorNode(
        id="op_hub",
        svcId="svc_alpha",
        spec=_patch_hub_spec(state_terminals=["cmd"]),
    )
    service = _FakeServiceNode(
        id="svc_target",
        spec=F8ServiceSpec(
            serviceClass="f8.test.target",
            label="Target",
            commands=[F8Command(name="Run Value", showOnNode=True, params=[])],
        ),
    )

    source.add_output_port("value[S]").connect_to(hub.add_input_port("[S]cmd"))
    hub.add_output_port("cmd[S]").connect_to(service.add_input_port("[C]Run Value"))

    graph = compile_global_runtime_graph(services=[], operators=[source, hub], service_nodes=[service])

    assert "op_hub" not in {str(node.nodeId) for node in list(graph.nodes or [])}
    lowered_edges = [edge for edge in list(graph.edges or []) if edge.kind == F8EdgeKindEnum.state]
    assert len(lowered_edges) == 1
    edge = lowered_edges[0]
    assert str(edge.fromOperatorId or "") == "op_source"
    assert str(edge.fromPort or "") == "value"
    assert str(edge.toOperatorId or "") == "svc_target"
    assert str(edge.toPort or "") == command_input_state_field("Run Value")


def test_runtime_compiler_lowers_nested_patch_hubs() -> None:
    source = _FakeOperatorNode(
        id="op_source",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.source",
            label="Source",
            dataOutPorts=[F8DataPortSpec(name="out", valueSchema=any_schema(), required=False)],
        ),
    )
    hub_a = _FakeOperatorNode(id="op_hub_a", svcId="svc_alpha", spec=_patch_hub_spec(data_terminals=["feed"]))
    hub_b = _FakeOperatorNode(id="op_hub_b", svcId="svc_alpha", spec=_patch_hub_spec(data_terminals=["feed"]))
    sink = _FakeOperatorNode(
        id="op_sink",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.sink",
            label="Sink",
            dataInPorts=[F8DataPortSpec(name="in", valueSchema=any_schema(), required=False)],
        ),
    )

    source.add_output_port("out[D]").connect_to(hub_a.add_input_port("[D]feed"))
    hub_a.add_output_port("feed[D]").connect_to(hub_b.add_input_port("[D]feed"))
    hub_b.add_output_port("feed[D]").connect_to(sink.add_input_port("[D]in"))

    graph = compile_global_runtime_graph(services=[], operators=[source, hub_a, hub_b, sink], service_nodes=[])

    lowered_edges = [edge for edge in list(graph.edges or []) if edge.kind == F8EdgeKindEnum.data]
    assert len(lowered_edges) == 1
    edge = lowered_edges[0]
    assert str(edge.fromOperatorId or "") == "op_source"
    assert str(edge.toOperatorId or "") == "op_sink"


def test_runtime_compiler_rejects_patch_hub_multi_upstream() -> None:
    source_a = _FakeOperatorNode(
        id="op_source_a",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.source",
            label="Source A",
            dataOutPorts=[F8DataPortSpec(name="out", valueSchema=any_schema(), required=False)],
        ),
    )
    source_b = _FakeOperatorNode(
        id="op_source_b",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.source",
            label="Source B",
            dataOutPorts=[F8DataPortSpec(name="out", valueSchema=any_schema(), required=False)],
        ),
    )
    hub = _FakeOperatorNode(id="op_hub", svcId="svc_alpha", spec=_patch_hub_spec(data_terminals=["feed"]))
    sink = _FakeOperatorNode(
        id="op_sink",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.sink",
            label="Sink",
            dataInPorts=[F8DataPortSpec(name="in", valueSchema=any_schema(), required=False)],
        ),
    )

    source_a.add_output_port("out[D]").connect_to(hub.add_input_port("[D]feed"))
    source_b.add_output_port("out[D]").connect_to(hub.add_input_port("[D]feed"))
    hub.add_output_port("feed[D]").connect_to(sink.add_input_port("[D]in"))

    try:
        compile_global_runtime_graph(services=[], operators=[source_a, source_b, hub, sink], service_nodes=[])
    except ValueError as exc:
        assert "multiple upstreams" in str(exc)
    else:
        raise AssertionError("expected patch hub multi-upstream compile error")


def test_runtime_compiler_rejects_patch_hub_cycles() -> None:
    hub_a = _FakeOperatorNode(id="op_hub_a", svcId="svc_alpha", spec=_patch_hub_spec(data_terminals=["feed"]))
    hub_b = _FakeOperatorNode(id="op_hub_b", svcId="svc_alpha", spec=_patch_hub_spec(data_terminals=["feed"]))

    hub_a.add_output_port("feed[D]").connect_to(hub_b.add_input_port("[D]feed"))
    hub_b.add_output_port("feed[D]").connect_to(hub_a.add_input_port("[D]feed"))

    try:
        compile_global_runtime_graph(services=[], operators=[hub_a, hub_b], service_nodes=[])
    except ValueError as exc:
        assert "cycle" in str(exc)
    else:
        raise AssertionError("expected patch hub cycle compile error")


def test_runtime_compiler_warns_on_patch_hub_dangling_terminals() -> None:
    source = _FakeOperatorNode(
        id="op_source",
        svcId="svc_alpha",
        spec=F8OperatorSpec(
            serviceClass="f8.test.service",
            operatorClass="f8.test.source",
            label="Source",
            dataOutPorts=[F8DataPortSpec(name="out", valueSchema=any_schema(), required=False)],
        ),
    )
    hub = _FakeOperatorNode(id="op_hub", svcId="svc_alpha", spec=_patch_hub_spec(data_terminals=["feed"]))

    source.add_output_port("out[D]").connect_to(hub.add_input_port("[D]feed"))

    warnings: list[str] = []
    graph = compile_global_runtime_graph(
        services=[],
        operators=[source, hub],
        service_nodes=[],
        compile_warnings=warnings,
    )

    assert not list(graph.edges or [])
    assert any("no downstream consumers" in warning for warning in warnings)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_service_node_draw_places_command_ports_beside_row_and_hides_labels() -> None:
    _ensure_app()
    spec = F8ServiceSpec(
        serviceClass="f8.test.service",
        label="Service",
        commands=[F8Command(name="stopTracking", showOnNode=True, params=[])],
        stateFields=[F8StateSpec(name="value", valueSchema=number_schema(), access=F8StateAccess.rw, showOnNode=True)],
    )
    node_cls = type(
        "TmpServiceNode",
        (F8StudioServiceBaseNode,),
        {"__identifier__": "svc", "NODE_NAME": spec.label, "SPEC_TEMPLATE": spec},
    )
    node = node_cls()
    view = node.view

    header = QtWidgets.QWidget()
    header_layout = QtWidgets.QHBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.addWidget(QtWidgets.QLabel("stopTracking"))

    widget = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(header)
    proxy = QtWidgets.QGraphicsProxyWidget(view)
    proxy.setWidget(widget)
    view._command_inline_proxies["stopTracking"] = proxy
    view._command_inline_headers["stopTracking"] = header
    view._command_inline_buttons["stopTracking"] = QtWidgets.QToolButton()
    view._command_inline_descriptions["stopTracking"] = ""
    view._command_inline_serials["stopTracking"] = "serial"
    view._width = 260.0
    view._height = 180.0
    view._prepare_layout_metrics()

    in_port = next(port for port in view.inputs if port.name == "[C]stopTracking")
    out_port = next(port for port in view.outputs if port.name == "stopTracking[C]")
    in_text = view.get_input_text_item(in_port)
    out_text = view.get_output_text_item(out_port)
    view._align_ports_horizontal(18.0)
    view._set_port_text_visibility(visible=True)
    proxy_pos = proxy.pos()
    header_height = max(float(header.sizeHint().height()), float(in_port.boundingRect().height()))
    header_mid_y = float(proxy_pos.y()) + (header_height / 2.0)

    assert not in_text.isVisible()
    assert not out_text.isVisible()
    assert in_port.pos().x() < proxy.pos().x()
    assert out_port.pos().x() > proxy.pos().x() + header.sizeHint().width()
    assert abs((float(in_port.pos().y()) + float(in_port.boundingRect().height()) / 2.0) - header_mid_y) < 2.0
    assert abs((float(out_port.pos().y()) + float(out_port.boundingRect().height()) / 2.0) - header_mid_y) < 2.0


def test_sync_from_spec_hides_command_ports_and_rows_when_show_on_node_turns_off() -> None:
    _ensure_app()
    spec = F8ServiceSpec(
        serviceClass="f8.test.service",
        label="Service",
        commands=[F8Command(name="stopTracking", showOnNode=True, params=[])],
    )
    node_cls = type(
        "TmpServiceNodeToggle",
        (F8StudioServiceBaseNode,),
        {"__identifier__": "svc", "NODE_NAME": spec.label, "SPEC_TEMPLATE": spec},
    )
    node = node_cls()
    view = node.view
    view._backend_node = lambda: node  # type: ignore[method-assign]

    view._ensure_inline_command_rows()
    assert "[C]stopTracking" in node.inputs()
    assert "stopTracking[C]" in node.outputs()
    assert "stopTracking" in view._command_inline_proxies

    node.spec.commands = [F8Command(name="stopTracking", showOnNode=False, params=[])]
    node.sync_from_spec()
    view._ensure_inline_command_rows()

    assert "[C]stopTracking" not in node.inputs()
    assert "stopTracking[C]" not in node.outputs()
    assert "stopTracking" not in view._command_inline_proxies


def test_operator_node_draw_places_command_ports_beside_inline_row() -> None:
    _ensure_app()
    spec = F8OperatorSpec(
        serviceClass="f8.test.service",
        operatorClass="f8.test.operator",
        label="Operator",
        commands=[F8Command(name="stopTracking", showOnNode=True, params=[])],
        stateFields=[F8StateSpec(name="value", valueSchema=number_schema(), access=F8StateAccess.rw, showOnNode=True)],
    )
    node_cls = type(
        "TmpOperatorNode",
        (F8StudioOperatorBaseNode,),
        {"__identifier__": "op", "NODE_NAME": spec.label, "SPEC_TEMPLATE": spec},
    )
    node = node_cls()
    node.svcId = "svc_parent"
    view = node.view
    view._backend_node = lambda: node  # type: ignore[method-assign]

    view._ensure_inline_command_rows()
    view._width = 260.0
    view._height = 180.0
    view._prepare_layout_metrics()

    in_port = next(port for port in view.inputs if port.name == "[C]stopTracking")
    out_port = next(port for port in view.outputs if port.name == "stopTracking[C]")
    view._align_ports_horizontal(18.0)
    proxy = view._command_inline_proxies["stopTracking"]
    header = view._command_inline_headers["stopTracking"]
    header_height = max(float(header.sizeHint().height()), float(in_port.boundingRect().height()))
    header_mid_y = float(proxy.pos().y()) + (header_height / 2.0)

    assert abs((float(in_port.pos().y()) + float(in_port.boundingRect().height()) / 2.0) - header_mid_y) < 2.0
    assert abs((float(out_port.pos().y()) + float(out_port.boundingRect().height()) / 2.0) - header_mid_y) < 2.0

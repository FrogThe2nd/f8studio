from __future__ import annotations

from f8pysdk.generated import F8DataPortSpec, F8ServiceSpec, F8StateAccess, F8StateSpec
from f8pysdk.schema_helpers import number_schema, string_schema

from f8pystudio.nodegraph.items import service_node_port_schema_actions as actions


class _BackendNode:
    def __init__(self, spec: F8ServiceSpec) -> None:
        self.spec = spec


class _NodeItemStub:
    def __init__(self, backend_node: _BackendNode) -> None:
        self._backend = backend_node

    def _backend_node(self) -> _BackendNode:
        return self._backend


def test_port_group_and_label_helpers() -> None:
    assert actions.port_group("[E]run") == "exec"
    assert actions.port_group("image[D]") == "data"
    assert actions.port_group("[S]gain") == "state"
    assert actions.port_group("plain") == "other"

    assert actions.display_port_label("[D]position") == "position"
    assert actions.display_port_label("output[S]") == "output"
    assert actions.display_port_label("[D]very_long_port_name", max_chars=8) == "very_lo..."


def test_schema_port_name_parse() -> None:
    assert actions.parse_schema_port_view_name("[D]in") == ("data", True, "in")
    assert actions.parse_schema_port_view_name("out[D]") == ("data", False, "out")
    assert actions.parse_schema_port_view_name("[S]gain") == ("state", True, "gain")
    assert actions.parse_schema_port_view_name("gain[S]") == ("state", False, "gain")
    assert actions.parse_schema_port_view_name("[E]next") is None


def test_data_and_state_tooltip_use_spec_schema_brief() -> None:
    spec = F8ServiceSpec(
        serviceClass="f8.tests.tooltip",
        label="Tooltip Test",
        dataInPorts=[
            F8DataPortSpec(
                name="gain",
                valueSchema=number_schema(),
                description="Input gain",
            )
        ],
        stateFields=[
            F8StateSpec(
                name="mode",
                valueSchema=string_schema(),
                access=F8StateAccess.rw,
                description="Runtime mode",
            )
        ],
    )
    node_item = _NodeItemStub(_BackendNode(spec))

    data_tip = actions.data_port_tooltip(node_item, is_in=True, port_name="gain")
    state_tip = actions.state_port_tooltip(node_item, is_in=True, field_name="mode")

    assert "schema: number" in data_tip
    assert "Input gain" in data_tip
    assert "schema: string" in state_tip
    assert "Runtime mode" in state_tip
    assert actions.port_tooltip_text(node_item, "[D]gain") == data_tip
    assert actions.port_tooltip_text(node_item, "[S]mode") == state_tip

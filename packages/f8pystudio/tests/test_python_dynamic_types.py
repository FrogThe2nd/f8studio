from __future__ import annotations

from f8pystudio.editor_assist.python_dynamic_types import (
    build_dynamic_inputs_stub,
    build_dynamic_outputs_stub,
    build_dynamic_states_stub,
)
from f8pystudio.editor_assist.workspace import (
    EditorAssistContext,
    EditorAssistDataInPort,
    EditorAssistDataOutPort,
    EditorAssistInputsBinding,
    EditorAssistOutputsBinding,
    EditorAssistStateField,
    EditorAssistStatesBinding,
    EditorWorkspaceSession,
)


def test_build_dynamic_inputs_stub_generates_expected_types() -> None:
    stub = build_dynamic_inputs_stub(
        type_name="F8Inputs",
        data_in_ports=(
            EditorAssistDataInPort(name="x", required=True, value_schema={"type": "number"}),
            EditorAssistDataInPort(
                name="y",
                required=False,
                description="Optional samples.",
                value_schema={"type": "array", "items": {"type": "integer"}},
            ),
            EditorAssistDataInPort(
                name="z",
                required=True,
                description="Nested payload.",
                value_schema={"type": "object", "properties": {"name": {"type": "string"}}},
            ),
        ),
        node_description="Execute custom python code.",
    )
    assert "class F8Inputs(_F8ObjectView):" in stub
    assert '"""Dynamic input payload view for python_script hooks. Node: Execute custom python code."""' in stub
    assert "x: float | None" in stub
    assert "# Optional samples." in stub
    assert "y: list[int] | None" in stub
    assert "class F8Inputs_z_obj(_F8ObjectView):" in stub
    assert '"""Nested object view for Nested payload.."""' in stub
    assert "name: str | None" in stub
    assert "z: F8Inputs_z_obj | None" in stub
    assert "class F8InputsTypes:" not in stub
    assert "F8Inputs_PORT_x_TYPE: TypeAlias = float" not in stub
    assert "F8Inputs_z_obj_Type: TypeAlias = F8Inputs_z_obj" not in stub
    assert "def is_port_x(value: Any, port: str) -> TypeGuard[float | None]: ..." in stub
    assert "def is_port_z(value: Any, port: str) -> TypeGuard[F8Inputs_z_obj | None]: ..." in stub
    assert "class F8OnDataHook(Protocol):" not in stub


def test_workspace_session_writes_dynamic_inputs_stub_module() -> None:
    session = EditorWorkspaceSession(
        language="python",
        context=EditorAssistContext(
            language="python",
            support_files=(("f8_script_api.pyi", "from f8_dynamic_inputs import F8Inputs\n"),),
            overlay_prefix="from f8_script_api import *",
            dynamic_inputs_binding=EditorAssistInputsBinding(
                source="data_in_ports",
                type_name="F8Inputs",
                module_name="f8_dynamic_inputs",
            ),
            data_in_ports=(EditorAssistDataInPort(name="value", required=True, value_schema={"type": "number"}, description="Numeric input."),),
            node_description="Workspace node.",
        ),
    )
    try:
        dynamic_file = session.root_path / "f8_dynamic_inputs.pyi"
        assert dynamic_file.is_file()
        dynamic_text = dynamic_file.read_text(encoding="utf-8")
        assert "class F8Inputs(_F8ObjectView):" in dynamic_text
        assert "Node: Workspace node." in dynamic_text
        assert "# Numeric input." in dynamic_text
        assert "value: float | None" in dynamic_text
        assert "class F8InputsTypes:" not in dynamic_text
        assert "F8Inputs_PORT_value_TYPE: TypeAlias = float" not in dynamic_text
        assert "def is_port_value(value: Any, port: str) -> TypeGuard[float | None]: ..." in dynamic_text
        assert "class F8OnDataHook(Protocol):" not in dynamic_text
    finally:
        session.close()


def test_build_dynamic_states_stub_generates_expected_types() -> None:
    stub = build_dynamic_states_stub(
        type_name="F8States",
        state_fields=(
            EditorAssistStateField(name="x", required=True, value_schema={"type": "number"}, access="rw"),
            EditorAssistStateField(
                name="pose",
                required=True,
                description="Latest pose state.",
                value_schema={"type": "object", "properties": {"x": {"type": "number"}}},
                access="ro",
            ),
            EditorAssistStateField(name="maybe", required=False, value_schema={"type": "string"}, access="rw", description="Optional label."),
        ),
        node_description="State-rich node.",
    )
    assert "class F8States(_F8ObjectView):" in stub
    assert "x: float | None" in stub
    assert "class F8States_pose_obj(_F8ObjectView):" in stub
    assert "# Optional label." in stub
    assert "pose: F8States_pose_obj | None" in stub
    assert "maybe: str | None" in stub
    assert "def is_state_x(value: Any, field: str) -> TypeGuard[float | None]: ..." in stub
    assert "def is_state_pose(value: Any, field: str) -> TypeGuard[F8States_pose_obj | None]: ..." in stub
    assert "class F8OnDataHook(Protocol):" not in stub


def test_workspace_session_writes_dynamic_states_stub_module() -> None:
    session = EditorWorkspaceSession(
        language="python",
        context=EditorAssistContext(
            language="python",
            support_files=(("f8_script_api.pyi", "from f8_dynamic_states import F8States\n"),),
            overlay_prefix="from f8_script_api import *",
            dynamic_states_binding=EditorAssistStatesBinding(
                source="state_fields",
                type_name="F8States",
                module_name="f8_dynamic_states",
            ),
            state_fields=(EditorAssistStateField(name="value", required=True, value_schema={"type": "number"}, access="rw", description="Visible state."),),
            node_description="State workspace node.",
        ),
    )
    try:
        dynamic_file = session.root_path / "f8_dynamic_states.pyi"
        assert dynamic_file.is_file()
        dynamic_text = dynamic_file.read_text(encoding="utf-8")
        assert "class F8States(_F8ObjectView):" in dynamic_text
        assert "Node: State workspace node." in dynamic_text
        assert "# Visible state." in dynamic_text
        assert "value: float | None" in dynamic_text
        assert "def is_state_value(value: Any, field: str) -> TypeGuard[float | None]: ..." in dynamic_text
        assert "class F8OnDataHook(Protocol):" not in dynamic_text
    finally:
        session.close()


def test_build_dynamic_outputs_stub_generates_expected_types() -> None:
    stub = build_dynamic_outputs_stub(
        type_name="F8Outputs",
        data_out_ports=(
            EditorAssistDataOutPort(name="result", required=False, value_schema={"type": "string"}, description="Primary output."),
            EditorAssistDataOutPort(name="meta-data", required=False, value_schema={"type": "number"}, description="Hidden output."),
        ),
        node_description="Output node.",
    )
    assert "class F8Outputs(_F8ObjectView):" in stub
    assert "Node: Output node." in stub
    assert "# Primary output." in stub
    assert "result: str | None" in stub
    assert "# - 'meta-data': Hidden output." in stub
    assert "def is_output_result(value: Any, port: str) -> TypeGuard[str | None]: ..." in stub
    assert "def is_output_meta_data(value: Any, port: str) -> TypeGuard[float | None]: ..." in stub


def test_workspace_session_writes_dynamic_outputs_stub_module() -> None:
    session = EditorWorkspaceSession(
        language="python",
        context=EditorAssistContext(
            language="python",
            support_files=(("f8_script_api.pyi", "from f8_dynamic_outputs import F8Outputs\n"),),
            overlay_prefix="from f8_script_api import *",
            dynamic_outputs_binding=EditorAssistOutputsBinding(
                source="data_out_ports",
                type_name="F8Outputs",
                module_name="f8_dynamic_outputs",
            ),
            data_out_ports=(EditorAssistDataOutPort(name="value", required=False, value_schema={"type": "number"}, description="Output value."),),
            node_description="Output workspace node.",
        ),
    )
    try:
        dynamic_file = session.root_path / "f8_dynamic_outputs.pyi"
        assert dynamic_file.is_file()
        dynamic_text = dynamic_file.read_text(encoding="utf-8")
        assert "class F8Outputs(_F8ObjectView):" in dynamic_text
        assert "Node: Output workspace node." in dynamic_text
        assert "# Output value." in dynamic_text
        assert "value: float | None" in dynamic_text
        assert "def is_output_value(value: Any, port: str) -> TypeGuard[float | None]: ..." in dynamic_text
    finally:
        session.close()


def test_build_dynamic_inputs_stub_handles_non_identifier_port_names() -> None:
    stub = build_dynamic_inputs_stub(
        type_name="F8Inputs",
        data_in_ports=(
            EditorAssistDataInPort(name="pose-2d", required=True, value_schema={"type": "number"}),
        ),
    )
    assert "class F8Inputs(_F8ObjectView):" in stub
    assert "pose-2d" in stub
    assert "pose-2d: float" not in stub
    assert "F8Inputs_PORT_pose_2d_TYPE: TypeAlias = float" not in stub
    assert "def is_port_pose_2d(value: Any, port: str) -> TypeGuard[float | None]: ..." in stub


def test_build_dynamic_inputs_stub_maps_keyword_port_to_alias_attribute() -> None:
    stub = build_dynamic_inputs_stub(
        type_name="F8Inputs",
        data_in_ports=(
            EditorAssistDataInPort(name="in", required=True, value_schema={"type": "number"}),
        ),
    )
    assert "class F8Inputs(_F8ObjectView):" in stub
    assert "in_: float | None" in stub
    assert "in: float" not in stub
    assert "F8Inputs_PORT_in_TYPE: TypeAlias = float" not in stub
    assert "def is_port_in(value: Any, port: str) -> TypeGuard[float | None]: ..." in stub


def test_build_dynamic_states_stub_handles_non_identifier_state_names() -> None:
    stub = build_dynamic_states_stub(
        type_name="F8States",
        state_fields=(
            EditorAssistStateField(name="pose-2d", required=True, value_schema={"type": "number"}, access="rw"),
        ),
    )
    assert "class F8States(_F8ObjectView):" in stub
    assert "pose-2d" in stub
    assert "pose-2d: float" not in stub
    assert "def is_state_pose_2d(value: Any, field: str) -> TypeGuard[float | None]: ..." in stub


def test_build_dynamic_states_stub_maps_keyword_state_to_alias_attribute() -> None:
    stub = build_dynamic_states_stub(
        type_name="F8States",
        state_fields=(
            EditorAssistStateField(name="in", required=True, value_schema={"type": "number"}, access="rw"),
        ),
    )
    assert "class F8States(_F8ObjectView):" in stub
    assert "in_: float | None" in stub
    assert "def is_state_in(value: Any, field: str) -> TypeGuard[float | None]: ..." in stub


def test_build_dynamic_states_stub_dedupes_guard_names() -> None:
    stub = build_dynamic_states_stub(
        type_name="F8States",
        state_fields=(
            EditorAssistStateField(name="a-b", required=True, value_schema={"type": "number"}, access="rw"),
            EditorAssistStateField(name="a_b", required=True, value_schema={"type": "number"}, access="rw"),
        ),
    )
    assert "def is_state_a_b(value: Any, field: str) -> TypeGuard[float | None]: ..." in stub
    assert "def is_state_a_b_1(value: Any, field: str) -> TypeGuard[float | None]: ..." in stub

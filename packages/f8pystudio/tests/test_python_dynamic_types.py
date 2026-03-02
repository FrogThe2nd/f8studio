from __future__ import annotations

from f8pystudio.editor_assist.python_dynamic_types import build_dynamic_inputs_stub, build_dynamic_states_stub
from f8pystudio.editor_assist.workspace import (
    EditorAssistContext,
    EditorAssistDataInPort,
    EditorAssistInputsBinding,
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
                value_schema={"type": "array", "items": {"type": "integer"}},
            ),
            EditorAssistDataInPort(
                name="z",
                required=True,
                value_schema={"type": "object", "properties": {"name": {"type": "string"}}},
            ),
        ),
    )
    assert "class F8Inputs(_F8ObjectView):" in stub
    assert "x: float" in stub
    assert "y: list[int] | None" in stub
    assert "class F8Inputs_z_obj(_F8ObjectView):" in stub
    assert "name: str | None" in stub
    assert "z: F8Inputs_z_obj" in stub


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
            data_in_ports=(EditorAssistDataInPort(name="value", required=True, value_schema={"type": "number"}),),
        ),
    )
    try:
        dynamic_file = session.root_path / "f8_dynamic_inputs.pyi"
        assert dynamic_file.is_file()
        dynamic_text = dynamic_file.read_text(encoding="utf-8")
        assert "class F8Inputs(_F8ObjectView):" in dynamic_text
        assert "value: float" in dynamic_text
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
                value_schema={"type": "object", "properties": {"x": {"type": "number"}}},
                access="ro",
            ),
            EditorAssistStateField(name="maybe", required=False, value_schema={"type": "string"}, access="rw"),
        ),
    )
    assert "class F8States(_F8ObjectView):" in stub
    assert "x: float" in stub
    assert "class F8States_pose_obj(_F8ObjectView):" in stub
    assert "pose: F8States_pose_obj" in stub
    assert "maybe: str | None" in stub


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
            state_fields=(EditorAssistStateField(name="value", required=True, value_schema={"type": "number"}, access="rw"),),
        ),
    )
    try:
        dynamic_file = session.root_path / "f8_dynamic_states.pyi"
        assert dynamic_file.is_file()
        dynamic_text = dynamic_file.read_text(encoding="utf-8")
        assert "class F8States(_F8ObjectView):" in dynamic_text
        assert "value: float" in dynamic_text
    finally:
        session.close()

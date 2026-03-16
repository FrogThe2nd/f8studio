from __future__ import annotations

import json
import logging
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .python_dynamic_types import (
    build_dynamic_inputs_stub,
    build_dynamic_outputs_stub,
    build_dynamic_states_stub,
)
from .pyscript_stubs import write_support_files

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EditorAssistDataInPort:
    name: str
    required: bool = True
    value_schema: dict[str, Any] | None = None
    description: str = ""


@dataclass(frozen=True)
class EditorAssistDataOutPort:
    name: str
    required: bool = True
    value_schema: dict[str, Any] | None = None
    description: str = ""


@dataclass(frozen=True)
class EditorAssistInputsBinding:
    source: str = "data_in_ports"
    type_name: str = "F8Inputs"
    module_name: str = "f8_dynamic_inputs"
    schema_mode: str = "basic_recursive"
    access_mode: str = "object_and_mapping"


@dataclass(frozen=True)
class EditorAssistOutputsBinding:
    source: str = "data_out_ports"
    type_name: str = "F8Outputs"
    module_name: str = "f8_dynamic_outputs"
    schema_mode: str = "basic_recursive"
    access_mode: str = "object_and_mapping"


@dataclass(frozen=True)
class EditorAssistStateField:
    name: str
    required: bool = False
    value_schema: dict[str, Any] | None = None
    access: str = "rw"
    description: str = ""


@dataclass(frozen=True)
class EditorAssistStatesBinding:
    source: str = "state_fields"
    type_name: str = "F8States"
    module_name: str = "f8_dynamic_states"
    schema_mode: str = "basic_recursive"
    access_mode: str = "object_and_mapping"


@dataclass(frozen=True)
class EditorAssistContext:
    language: str = "plaintext"
    node_kind: str = ""
    service_class: str = ""
    operator_class: str = ""
    node_description: str = ""
    node_instance_purpose: str = ""
    target_field_kind: str = ""
    target_field_name: str = ""
    target_field_label: str = ""
    target_field_description: str = ""
    target_ui_language: str = ""
    target_value_schema: dict[str, Any] | None = None
    support_files: tuple[tuple[str, str], ...] = ()
    overlay_prefix: str = ""
    dynamic_inputs_binding: EditorAssistInputsBinding | None = None
    data_in_ports: tuple[EditorAssistDataInPort, ...] = ()
    dynamic_outputs_binding: EditorAssistOutputsBinding | None = None
    data_out_ports: tuple[EditorAssistDataOutPort, ...] = ()
    dynamic_states_binding: EditorAssistStatesBinding | None = None
    state_fields: tuple[EditorAssistStateField, ...] = ()
    error_message: str = ""


@dataclass(frozen=True)
class LspDocumentSnapshot:
    text: str
    line_offset: int


class EditorWorkspaceSession:
    """Per-editor temporary workspace used by the language server."""

    def __init__(self, *, language: str, context: EditorAssistContext | None = None) -> None:
        lang = str(language or "plaintext").strip().lower() or "plaintext"
        if lang != "python":
            raise ValueError(f"Language server is only supported for python, got {lang!r}")

        self._language = lang
        self._context = context or EditorAssistContext()

        root = Path(tempfile.gettempdir()) / "f8studio" / "editor_assist" / uuid.uuid4().hex
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

        self._source_path = self._root / "session_script.py"
        support_files = list(self._context.support_files)
        if self._context.dynamic_inputs_binding is not None:
            binding = self._context.dynamic_inputs_binding
            module_path = str(binding.module_name or "").strip().replace(".", "/")
            if module_path:
                support_files.append(
                    (
                        f"{module_path}.pyi",
                        build_dynamic_inputs_stub(
                            type_name=str(binding.type_name or "F8Inputs"),
                            data_in_ports=tuple(self._context.data_in_ports),
                            node_description=str(self._context.node_description or ""),
                        ),
                    )
                )
        if self._context.dynamic_states_binding is not None:
            binding = self._context.dynamic_states_binding
            module_path = str(binding.module_name or "").strip().replace(".", "/")
            if module_path:
                support_files.append(
                    (
                        f"{module_path}.pyi",
                        build_dynamic_states_stub(
                            type_name=str(binding.type_name or "F8States"),
                            state_fields=tuple(self._context.state_fields),
                            node_description=str(self._context.node_description or ""),
                        ),
                    )
                )
        if self._context.dynamic_outputs_binding is not None:
            binding = self._context.dynamic_outputs_binding
            module_path = str(binding.module_name or "").strip().replace(".", "/")
            if module_path:
                support_files.append(
                    (
                        f"{module_path}.pyi",
                        build_dynamic_outputs_stub(
                            type_name=str(binding.type_name or "F8Outputs"),
                            data_out_ports=tuple(self._context.data_out_ports),
                            node_description=str(self._context.node_description or ""),
                        ),
                    )
                )

        overlay = write_support_files(
            self._root,
            support_files=tuple(support_files),
            overlay_prefix=str(self._context.overlay_prefix or ""),
        )
        if overlay and not overlay.endswith("\n"):
            overlay = f"{overlay}\n"
        self._line_overlay_prefix = overlay
        self._line_offset = self._line_overlay_prefix.count("\n")
        self._write_pyright_config()
        logger.debug(
            "editor assist workspace prepared: root=%s supportFiles=%s dynamicInputs=%s dynamicOutputs=%s dynamicStates=%s overlayLines=%d",
            self._root,
            [name for name, _ in support_files],
            self._context.dynamic_inputs_binding is not None,
            self._context.dynamic_outputs_binding is not None,
            self._context.dynamic_states_binding is not None,
            self._line_offset,
        )

    @property
    def root_path(self) -> Path:
        return self._root

    @property
    def source_path(self) -> Path:
        return self._source_path

    @property
    def document_uri(self) -> str:
        return self._source_path.as_uri()

    @property
    def line_offset(self) -> int:
        return int(self._line_offset)

    def build_document_snapshot(self, *, user_code: str) -> LspDocumentSnapshot:
        code = str(user_code or "")
        overlay = str(self._line_overlay_prefix)
        if overlay:
            text = f"{overlay}{code}"
        else:
            text = code
        self._source_path.write_text(text, encoding="utf-8")
        return LspDocumentSnapshot(text=text, line_offset=int(self._line_offset))

    def close(self) -> None:
        try:
            shutil.rmtree(self._root)
        except OSError:
            logger.exception("Failed to cleanup editor workspace: %s", self._root)

    def _write_pyright_config(self) -> None:
        cfg = {
            "$schema": "https://raw.githubusercontent.com/microsoft/pyright/main/packages/pyright/schema/pyrightconfig.schema.json",
            "include": [str(self._source_path.name)],
            "typeCheckingMode": "basic",
            "pythonVersion": "3.10",
            "reportMissingImports": "warning",
            "reportMissingTypeStubs": "none",
            "extraPaths": ["."],
        }
        (self._root / "pyrightconfig.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from .pyscript_stubs import write_support_files

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EditorAssistContext:
    mode: str = "python"
    service_class: str = ""
    operator_class: str = ""
    state_field_name: str = ""
    data_in_ports: tuple[str, ...] = ()
    data_out_ports: tuple[str, ...] = ()
    state_fields: tuple[str, ...] = ()


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
        self._line_overlay_prefix = write_support_files(
            self._root,
            mode=str(self._context.mode or "python"),
            data_in_ports=tuple(self._context.data_in_ports),
            data_out_ports=tuple(self._context.data_out_ports),
            state_fields=tuple(self._context.state_fields),
        )
        self._line_offset = self._line_overlay_prefix.count("\n")
        self._write_pyright_config()

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

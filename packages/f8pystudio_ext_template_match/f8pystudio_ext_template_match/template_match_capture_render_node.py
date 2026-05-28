from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

from qtpy import QtWidgets

from f8pystudio.contracts.command_ui import CommandUiHandler, CommandUiSource
from f8pystudio.nodegraph.service_basenode import F8StudioServiceBaseNode

from .template_match_capture_dialog import TemplateCaptureFrame, TemplateMatchCaptureDialog


logger = logging.getLogger(__name__)
_RENDER_NODE_READ_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)
_CAPTURE_PARSE_ERRORS = (AttributeError, binascii.Error, RuntimeError, TypeError, ValueError)


class TemplateMatchCaptureRenderNode(F8StudioServiceBaseNode, CommandUiHandler):
    def handle_command_ui(
        self,
        cmd: Any,
        *,
        parent: QtWidgets.QWidget | None,
        source: CommandUiSource,
    ) -> bool:
        _ = source
        if self._command_name(cmd) != "captureTemplateFrame":
            return False
        self._open_template_match_capture_dialog(parent=parent)
        return True

    @staticmethod
    def _command_name(cmd: Any) -> str:
        if isinstance(cmd, dict):
            return str(cmd.get("name") or "").strip()
        try:
            return str(cmd.name or "").strip()
        except _RENDER_NODE_READ_ERRORS:
            logger.debug("failed to read template match command name", exc_info=True)
            return ""

    def _graph_or_none(self) -> Any | None:
        try:
            return self.graph
        except _RENDER_NODE_READ_ERRORS:
            logger.debug("failed to read template match render graph", exc_info=True)
            return None

    @staticmethod
    def _service_bridge_or_none(graph: Any | None) -> Any | None:
        if graph is None:
            return None
        try:
            return graph.service_bridge
        except _RENDER_NODE_READ_ERRORS:
            logger.debug("failed to read template match service bridge", exc_info=True)
            return None

    def _service_id(self) -> str:
        try:
            return str(self.id or "").strip()
        except _RENDER_NODE_READ_ERRORS:
            logger.debug("failed to read template match service id", exc_info=True)
            return ""

    @staticmethod
    def _capture_frame_from_result(result: dict[str, Any]) -> TemplateCaptureFrame:
        image_any = result.get("image")
        image_obj = image_any if isinstance(image_any, dict) else {}
        b64 = str(image_obj.get("b64") or "")
        raw = base64.b64decode(b64.encode("ascii"), validate=False) if b64 else b""
        return TemplateCaptureFrame(
            frame_id=int(result.get("frameId") or 0),
            ts_ms=int(result.get("tsMs") or 0),
            image_bytes=raw,
            image_format=str(image_obj.get("format") or ""),
            width=int(image_obj.get("width") or 0),
            height=int(image_obj.get("height") or 0),
        )

    def _open_template_match_capture_dialog(self, *, parent: QtWidgets.QWidget | None) -> None:
        graph = self._graph_or_none()
        bridge = self._service_bridge_or_none(graph)
        sid = self._service_id()
        if bridge is None or not sid:
            return

        def _request_capture(done) -> None:
            def _cb(result: dict[str, Any] | None, err: str | None) -> None:
                if err:
                    done(None, err)
                    return
                if not isinstance(result, dict):
                    done(None, "invalid response")
                    return
                try:
                    cap = self._capture_frame_from_result(result)
                except _CAPTURE_PARSE_ERRORS as exc:
                    logger.debug("failed to parse template match capture response", exc_info=True)
                    done(None, str(exc))
                    return
                done(cap, None)

            try:
                bridge.request_remote_command(sid, "captureTemplateFrame", {}, _cb)
            except _RENDER_NODE_READ_ERRORS as exc:
                logger.debug("failed to request template match capture frame service_id=%s", sid, exc_info=True)
                done(None, str(exc))

        def _set_template_image_b64(b64: str) -> None:
            bridge.set_remote_state(sid, sid, "templateImagePngB64", str(b64 or ""))

        dialog = TemplateMatchCaptureDialog(
            parent=parent,
            bridge=bridge,
            service_id=sid,
            request_capture=_request_capture,
            set_template_b64=_set_template_image_b64,
        )
        dialog.exec()

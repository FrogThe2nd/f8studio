from __future__ import annotations

import base64
from typing import Any

from qtpy import QtWidgets

from f8pystudio.command_ui_protocol import CommandUiHandler, CommandUiSource
from f8pystudio.nodegraph.service_basenode import F8StudioServiceBaseNode

from .template_match_capture_dialog import TemplateCaptureFrame, TemplateMatchCaptureDialog


class TemplateMatchCaptureRenderNode(F8StudioServiceBaseNode, CommandUiHandler):
    def handle_command_ui(
        self,
        cmd: Any,
        *,
        parent: QtWidgets.QWidget | None,
        source: CommandUiSource,
    ) -> bool:
        _ = source
        if isinstance(cmd, dict):
            call = str(cmd.get("name") or "").strip()
        else:
            try:
                call = str(cmd.name or "").strip()
            except Exception:
                call = ""
        if call != "captureTemplateFrame":
            return False
        self._open_template_match_capture_dialog(parent=parent)
        return True

    def _open_template_match_capture_dialog(self, *, parent: QtWidgets.QWidget | None) -> None:
        graph = None
        try:
            graph = self.graph
        except Exception:
            graph = None
        bridge = None
        try:
            bridge = graph.service_bridge if graph is not None else None
        except Exception:
            bridge = None

        try:
            sid = str(self.id or "").strip()
        except Exception:
            sid = ""
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
                    image_obj = result.get("image") if isinstance(result.get("image"), dict) else {}
                    b64 = str(image_obj.get("b64") or "")
                    raw = base64.b64decode(b64.encode("ascii"), validate=False) if b64 else b""
                    cap = TemplateCaptureFrame(
                        frame_id=int(result.get("frameId") or 0),
                        ts_ms=int(result.get("tsMs") or 0),
                        image_bytes=raw,
                        image_format=str(image_obj.get("format") or ""),
                        width=int(image_obj.get("width") or 0),
                        height=int(image_obj.get("height") or 0),
                    )
                except Exception as exc:
                    done(None, str(exc))
                    return
                done(cap, None)

            try:
                bridge.request_remote_command(sid, "captureTemplateFrame", {}, _cb)
            except Exception as exc:
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


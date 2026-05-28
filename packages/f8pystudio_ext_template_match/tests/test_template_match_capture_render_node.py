from __future__ import annotations

from types import SimpleNamespace

from f8pystudio_ext_template_match.template_match_capture_render_node import TemplateMatchCaptureRenderNode


def test_capture_frame_from_result_decodes_image_payload() -> None:
    frame = TemplateMatchCaptureRenderNode._capture_frame_from_result(
        {
            "frameId": "7",
            "tsMs": "123",
            "image": {
                "b64": "aGVsbG8=",
                "format": "png",
                "width": "2",
                "height": "3",
            },
        }
    )

    assert frame.frame_id == 7
    assert frame.ts_ms == 123
    assert frame.image_bytes == b"hello"
    assert frame.image_format == "png"
    assert frame.width == 2
    assert frame.height == 3


def test_command_name_logs_unreadable_command(caplog) -> None:
    class _BrokenCommand:
        @property
        def name(self) -> str:
            raise RuntimeError("command deleted")

    with caplog.at_level("DEBUG", logger="f8pystudio_ext_template_match.template_match_capture_render_node"):
        name = TemplateMatchCaptureRenderNode._command_name(_BrokenCommand())

    assert name == ""
    assert "failed to read template match command name" in caplog.text


def test_capture_request_reports_parse_error(monkeypatch) -> None:
    node = TemplateMatchCaptureRenderNode.__new__(TemplateMatchCaptureRenderNode)
    bridge = SimpleNamespace()
    callback_results: list[tuple[object, str | None]] = []

    def _request_remote_command(_sid: str, _name: str, _args: object, callback) -> None:
        callback({"frameId": object(), "image": {}}, None)

    bridge.request_remote_command = _request_remote_command
    monkeypatch.setattr(node, "_graph_or_none", lambda: SimpleNamespace(service_bridge=bridge))
    monkeypatch.setattr(node, "_service_id", lambda: "svc-template")
    monkeypatch.setattr(
        "f8pystudio_ext_template_match.template_match_capture_render_node.TemplateMatchCaptureDialog",
        lambda **kwargs: SimpleNamespace(
            exec=lambda: kwargs["request_capture"](
                lambda frame, err: callback_results.append((frame, None if err is None else str(err)))
            )
        ),
    )

    node._open_template_match_capture_dialog(parent=None)

    assert len(callback_results) == 1
    assert callback_results[0][0] is None
    assert callback_results[0][1]

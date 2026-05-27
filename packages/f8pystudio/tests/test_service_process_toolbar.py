from __future__ import annotations

from qtpy import QtWidgets

from f8pystudio.nodegraph.service_process_toolbar import ServiceProcessToolbar


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_toolbar_provider_failure_is_logged_once(monkeypatch) -> None:
    _ensure_app()

    def _raise_bridge() -> object:
        raise RuntimeError("bridge provider failed")

    debug_messages: list[str] = []

    def _debug(message: str, *args: object, **kwargs: object) -> None:
        assert kwargs.get("exc_info") is not None
        debug_messages.append(str(message))

    monkeypatch.setattr("f8pystudio.nodegraph.service_process_toolbar.logger.debug", _debug)
    toolbar = ServiceProcessToolbar(service_id="svcA", get_bridge=_raise_bridge, get_node=lambda: object())
    toolbar.refresh()
    toolbar.refresh()

    assert toolbar._btn_toggle.isEnabled() is False
    assert sum("Service toolbar bridge provider failed" in message for message in debug_messages) == 1
    toolbar.close()


def test_toolbar_starts_service_with_compiled_graph() -> None:
    _ensure_app()

    class _Bridge:
        def __init__(self) -> None:
            self.start_calls: list[tuple[str, str, object]] = []

        def request_service_status(self, service_id: str) -> None:
            assert service_id == "svcA"

        def is_service_running(self, service_id: str) -> bool:
            assert service_id == "svcA"
            return False

        def start_service_and_deploy(self, service_id: str, *, service_class: str, compiled: object | None = None) -> None:
            self.start_calls.append((str(service_id), str(service_class), compiled))

    bridge = _Bridge()
    compiled = object()
    toolbar = ServiceProcessToolbar(
        service_id="svcA",
        get_bridge=lambda: bridge,
        get_node=lambda: object(),
        get_service_class=lambda: "f8.tests.service",
        get_compiled_graphs=lambda: compiled,
    )

    toolbar._on_toggle_clicked()

    assert bridge.start_calls == [("svcA", "f8.tests.service", compiled)]
    toolbar.close()

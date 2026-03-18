from __future__ import annotations

from typing import Any

from qtpy import QtWidgets

from f8pystudio.nodegraph.items.state_inline_controls import refresh_state_inline_option_pools
from f8pystudio.widgets.editor_controls import F8OptionCombo


class _FakeBackendNode:
    def __init__(self, props: dict[str, Any]) -> None:
        self._props = dict(props)

    def get_property(self, name: str) -> Any:
        return self._props.get(str(name), None)


class _FakeNodeItem:
    def __init__(self, props: dict[str, Any]) -> None:
        self._node = _FakeBackendNode(props)
        self._state_inline_option_pools: dict[str, str] = {"choice": "choices"}
        self._state_inline_controls: dict[str, Any] = {"choice": F8OptionCombo()}

    def _backend_node(self) -> _FakeBackendNode:
        return self._node


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        return app
    return QtWidgets.QApplication([])


def test_refresh_option_pool_parses_json_pool_and_reapplies_backend_selection() -> None:
    _ensure_app()

    item = _FakeNodeItem(
        {
            "choices": '["alpha", "beta"]',
            "choice": "beta",
        }
    )
    combo = item._state_inline_controls["choice"]
    combo.set_options([], labels=[])
    combo.set_value("beta")
    assert combo.value() is None

    refresh_state_inline_option_pools(item, "choices")

    assert combo.value() == "beta"

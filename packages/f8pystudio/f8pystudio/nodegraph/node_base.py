from __future__ import annotations

from f8pysdk.msgspec_codec import copy_model, dump_json, validate_as
import copy
import json
from typing import Any

from NodeGraphQt import BaseNode
from NodeGraphQt.nodes.base_node import NodeBaseWidget
from NodeGraphQt.errors import NodeWidgetError

from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec
from f8pysdk.specs import coerce_spec_payload
from .ui_override_mutations import apply_named_order, get_list_order_override

from .node_model import F8StudioNodeModel
from .layers import extract_node_layer_ids_from_ui_state, set_node_layer_ids_in_ui_state


class F8StudioBaseNode(BaseNode):
    """
    Studio base node that persists framework system data in the NodeGraphQt
    session via default node model fields (not custom properties).
    """

    # Class-level spec template for building instance `spec`.
    SPEC_TEMPLATE: F8OperatorSpec | F8ServiceSpec | dict | None = None

    def __init__(self, qgraphics_item=None):
        super().__init__(qgraphics_item=qgraphics_item)
        # NodeGraphQt calls `node.update()` inside `set_model()`. Initialize
        # any fields used by `update()` before attaching the model.
        self._last_spec_obj: F8OperatorSpec | F8ServiceSpec | None = None
        self._last_ui_serial: str = ""
        self.set_model(F8StudioNodeModel())
        # Allow model-level property setters (used by session deserialization) to
        # trigger a spec/UI-driven rebuild before restoring custom properties.
        self.model._owner_node = self  # type: ignore[attr-defined]

        template = type(self).SPEC_TEMPLATE
        if template is None:
            raise RuntimeError(f"{self.__class__.__name__} must define `SPEC_TEMPLATE`.")

        if isinstance(template, F8OperatorSpec):
            spec = validate_as(F8OperatorSpec, dump_json(template, mode="json"))
        elif isinstance(template, F8ServiceSpec):
            spec = validate_as(F8ServiceSpec, dump_json(template, mode="json"))
        elif isinstance(template, dict):
            spec = coerce_spec_payload(template)
        else:
            spec = copy.deepcopy(template)

        self.set_spec(spec, rebuild=False)
        self._last_ui_serial = self._ui_serial()

    @property
    def svcId(self) -> Any:
        return self.model.svcId

    @svcId.setter
    def svcId(self, value: Any) -> None:
        self.model.svcId = value

    @property
    def nodePurpose(self) -> str:
        return self.model.nodePurpose

    @nodePurpose.setter
    def nodePurpose(self, value: str) -> None:
        self.model.nodePurpose = value

    def update_model(self):
        """
        Extend NodeGraphQt model update so `spec` + system fields persist in
        session JSON.

        Also avoid writing values for ephemeral embedded widgets that are not
        backed by a registered node property.
        """
        for name, val in self.view.properties.items():
            if name in ["inputs", "outputs"]:
                continue
            if name not in self.model.properties and name not in self.model.custom_properties:
                continue
            self.model.set_property(name, val)

        for name, widget in self.view.widgets.items():
            if name not in self.model.properties and name not in self.model.custom_properties:
                continue
            self.model.set_property(name, widget.get_value())

        if not isinstance(self.model.f8_sys, dict):
            self.model.f8_sys = {}
        if not isinstance(self.model.f8_ui_overrides, dict):
            self.model.f8_ui_overrides = {}
        if not isinstance(self.model.f8_ui_state, dict):
            self.model.f8_ui_state = {}

    def add_ephemeral_widget(self, widget: NodeBaseWidget) -> None:
        """
        Add an embedded node widget without creating/persisting a custom node
        property for it.

        Use this for render-only UI panes whose state is persisted through
        explicit state fields instead of NodeGraphQt widget properties.
        """
        if not isinstance(widget, NodeBaseWidget):
            raise NodeWidgetError("'widget' must be an instance of a NodeBaseWidget")
        widget._node = self  # type: ignore[attr-defined]
        self.view.add_widget(widget)
        self.view.draw_node()
        widget.parent()

    def ui_overrides(self) -> dict[str, object]:
        return self.model.f8_ui_overrides if isinstance(self.model.f8_ui_overrides, dict) else {}

    def set_ui_overrides(self, value: dict[str, object] | None, *, rebuild: bool = True) -> None:
        self.model.set_property("f8_ui_overrides", value or {})
        self._last_ui_serial = self._ui_serial()
        if rebuild:
            self.sync_from_spec()

    def ui_state(self) -> dict[str, object]:
        return self.model.f8_ui_state if isinstance(self.model.f8_ui_state, dict) else {}

    def set_ui_state(self, value: dict[str, object] | None) -> None:
        self.model.set_property("f8_ui_state", value or {})

    def layer_ids(self) -> tuple[str, ...]:
        return extract_node_layer_ids_from_ui_state(self.ui_state())

    def set_layer_ids(self, layer_ids: list[str] | tuple[str, ...]) -> None:
        self.set_ui_state(set_node_layer_ids_in_ui_state(self.ui_state(), layer_ids=layer_ids))

    @staticmethod
    def _named_items_in_order(items: list[Any], *, order: list[str]) -> list[Any]:
        if not items:
            return []
        ordered_names = apply_named_order(
            base_names=[str(getattr(item, "name", "") or "").strip() for item in items],
            override_names=order,
        )
        items_by_name: dict[str, Any] = {}
        for item in items:
            name = str(getattr(item, "name", "") or "").strip()
            if not name or name in items_by_name:
                continue
            items_by_name[name] = item
        return [items_by_name[name] for name in ordered_names if name in items_by_name]

    def ordered_exec_port_names(self, *, is_in: bool) -> list[str]:
        spec = self.spec
        if not isinstance(spec, F8OperatorSpec):
            return []
        base_names = list(spec.execInPorts or []) if bool(is_in) else list(spec.execOutPorts or [])
        key = "execInPorts" if bool(is_in) else "execOutPorts"
        return apply_named_order(base_names=base_names, override_names=get_list_order_override(self, key=key))

    def ordered_data_port_specs(self, *, is_in: bool) -> list[Any]:
        spec = self.spec
        ports = list(spec.dataInPorts or []) if bool(is_in) else list(spec.dataOutPorts or [])
        key = "dataInPorts" if bool(is_in) else "dataOutPorts"
        return self._named_items_in_order(ports, order=get_list_order_override(self, key=key))

    def ordered_state_field_specs(self) -> list[Any]:
        return self._named_items_in_order(list(self.effective_state_fields() or []), order=get_list_order_override(self, key="stateFields"))

    def ordered_command_specs(self) -> list[Any]:
        return self._named_items_in_order(list(self.effective_commands() or []), order=get_list_order_override(self, key="commands"))

    def effective_state_fields(self):
        """
        Return state fields with UI overrides applied (showOnNode/uiControl/etc).
        """
        spec = self.spec
        fields = list(spec.stateFields or [])
        ui = self.ui_overrides()
        state_over = ui.get("stateFields") if isinstance(ui, dict) else None
        if isinstance(state_over, dict) and state_over and fields:
            allowed_keys = {"showOnNode", "uiControl", "label", "description"}
            out = []
            for f in fields:
                name = str(f.name or "").strip()
                ov = state_over.get(name) if name else None
                if not isinstance(ov, dict) or not ov:
                    out.append(f)
                    continue
                patch = {k: ov.get(k) for k in allowed_keys if k in ov}
                out.append(copy_model(f, update=patch))
            fields = out
        return self._named_items_in_order(fields, order=get_list_order_override(self, key="stateFields"))

    def effective_commands(self):
        """
        Return service commands with UI overrides applied.

        Currently only supports overriding `showOnNode` as UI-only customization.
        """
        spec = self.spec
        cmds = list(spec.commands or [])
        if not cmds:
            return cmds
        ui = self.ui_overrides()
        cmd_over = ui.get("commands") if isinstance(ui, dict) else None
        if isinstance(cmd_over, dict) and cmd_over:
            allowed_keys = {"showOnNode"}
            out = []
            for c in cmds:
                name = str(c.name or "").strip()
                ov = cmd_over.get(name) if name else None
                if not isinstance(ov, dict) or not ov:
                    out.append(c)
                    continue
                patch = {k: ov.get(k) for k in allowed_keys if k in ov}
                out.append(copy_model(c, update=patch))
            cmds = out
        return self._named_items_in_order(cmds, order=get_list_order_override(self, key="commands"))

    def data_port_show_on_node(self, name: str, *, is_in: bool) -> bool:
        """
        True if the data port should be rendered on the node body.

        Priority: UI override > spec field (if present) > default True.
        """
        n = str(name or "").strip()
        if not n:
            return True

        ui = self.ui_overrides()
        ports_over = ui.get("dataPorts") if isinstance(ui, dict) else None
        if isinstance(ports_over, dict):
            key = "in" if bool(is_in) else "out"
            dir_over = ports_over.get(key)
            if isinstance(dir_over, dict):
                ov = dir_over.get(n)
                if isinstance(ov, dict) and "showOnNode" in ov:
                    return bool(ov.get("showOnNode"))

        spec = self.spec
        ports = list(spec.dataInPorts or []) if bool(is_in) else list(spec.dataOutPorts or [])
        for p in ports:
            if str(p.name or "").strip() == n:
                return bool(p.showOnNode)

        return True

    def _ui_serial(self) -> str:
        try:
            ui = self.model.f8_ui_overrides if isinstance(self.model.f8_ui_overrides, dict) else {}
            return json.dumps(ui, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return ""

    def set_spec(self, value: F8OperatorSpec | F8ServiceSpec | dict, *, rebuild: bool = True) -> None:
        """
        Update the persistent spec stored on the model.

        `rebuild` controls whether `sync_from_spec()` is called after update.
        """
        self.model.set_property("f8_spec", value)
        self._last_spec_obj = self.model.f8_spec

        if rebuild:
            self.sync_from_spec()

    @property
    def spec(self) -> F8OperatorSpec | F8ServiceSpec:
        """
        Runtime view of the persisted model spec.
        """
        spec = self.model.f8_spec
        if not isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
            raise RuntimeError(f"{self.__class__.__name__} model is missing `f8_spec`.")
        return spec

    @spec.setter
    def spec(self, value: F8OperatorSpec | F8ServiceSpec | dict) -> None:
        self.set_spec(value, rebuild=True)

    def update(self):
        current = self.model.f8_spec
        has_spec = isinstance(current, (F8OperatorSpec, F8ServiceSpec))
        if has_spec and current is not self._last_spec_obj:
            self._last_spec_obj = current
            self.sync_from_spec()

        # During NodeGraphQt's `set_model()`, `update()` is called before we
        # have a spec. Avoid calling `sync_from_spec()` until `f8_spec` exists.
        ui_serial = self._ui_serial()
        if not has_spec:
            self._last_ui_serial = ui_serial
        else:
            last_ui_serial = self._last_ui_serial
            if ui_serial != last_ui_serial:
                self._last_ui_serial = ui_serial
                self.sync_from_spec()
        super().update()

    def sync_from_spec(self) -> None:
        """
        Hook for subclasses to rebuild ports/properties derived from `self.spec`.
        """
        return

    def on_graph_teardown(self) -> None:
        """
        Hook called before this node is removed from a graph session.

        Subclasses should release external resources (timers, windows, workers)
        and must keep this method idempotent.
        """
        return

    def is_missing_locked(self) -> bool:
        model = self.model
        if not isinstance(model.f8_sys, dict):
            model.f8_sys = {}
        return bool(model.f8_sys.get("missingLocked"))

    def missing_type(self) -> str:
        model = self.model
        if not isinstance(model.f8_sys, dict):
            model.f8_sys = {}
        return str(model.f8_sys.get("missingType") or "").strip()

    def missing_reason(self) -> str:
        model = self.model
        if not isinstance(model.f8_sys, dict):
            model.f8_sys = {}
        return str(model.f8_sys.get("missingReason") or "").strip()

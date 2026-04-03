from __future__ import annotations

from f8pysdk import F8ServiceSpec, F8StateAccess, F8StateSpec
from f8pysdk.msgspec_codec import copy_model
from f8pysdk.schema_helpers import string_schema

from f8pystudio.nodegraph.ui_override_mutations import (
    apply_named_order,
    remove_list_order_entry,
    rename_list_order_entry,
    set_list_order_override,
    set_state_field_ui_override,
)


class _TestNode:
    def __init__(self, spec: F8ServiceSpec) -> None:
        self.spec = spec
        self._ui_overrides: dict[str, object] = {}

    def ui_overrides(self) -> dict[str, object]:
        return dict(self._ui_overrides)

    def set_ui_overrides(self, value: dict[str, object] | None, *, rebuild: bool = True) -> None:
        _ = rebuild
        self._ui_overrides = dict(value or {})

    def effective_state_fields(self) -> list[F8StateSpec]:
        fields = list(self.spec.stateFields or [])
        state_over = self._ui_overrides.get("stateFields")
        if not isinstance(state_over, dict) or not state_over:
            return fields

        allowed_keys = {"showOnNode", "uiControl", "label", "description"}
        out: list[F8StateSpec] = []
        for field in fields:
            name = str(field.name or "").strip()
            override = state_over.get(name) if name else None
            if not isinstance(override, dict) or not override:
                out.append(field)
                continue
            patch = {key: override.get(key) for key in allowed_keys if key in override}
            out.append(copy_model(field, update=patch))
        return out


def _make_field(*, ui_control: str) -> F8StateSpec:
    return F8StateSpec(
        name="value",
        valueSchema=string_schema(),
        access=F8StateAccess.rw,
        uiControl=ui_control,
    )


def test_set_state_field_ui_override_removes_ui_control_when_cleared() -> None:
    node = _TestNode(F8ServiceSpec(serviceClass="f8.test", label="Test", stateFields=[_make_field(ui_control="")]))
    node.set_ui_overrides({"stateFields": {"value": {"uiControl": "dial"}}}, rebuild=False)

    base = _make_field(ui_control="")
    edited = _make_field(ui_control="")

    set_state_field_ui_override(node, field_name="value", base=base, edited=edited)

    assert node.ui_overrides() == {}


def test_effective_state_fields_fall_back_to_base_ui_control_after_clear() -> None:
    node = _TestNode(
        F8ServiceSpec(
        serviceClass="f8.test",
        label="Test",
        stateFields=[
            F8StateSpec(
                name="value",
                valueSchema=string_schema(),
                access=F8StateAccess.rw,
                uiControl="slider",
            )
        ],
    ))
    node.set_ui_overrides({"stateFields": {"value": {"uiControl": "dial"}}}, rebuild=False)

    base = F8StateSpec(
        name="value",
        valueSchema=string_schema(),
        access=F8StateAccess.rw,
        uiControl="slider",
    )
    edited = F8StateSpec(
        name="value",
        valueSchema=string_schema(),
        access=F8StateAccess.rw,
        uiControl="",
    )

    set_state_field_ui_override(node, field_name="value", base=base, edited=edited)

    fields = node.effective_state_fields()
    assert len(fields) == 1
    assert str(fields[0].uiControl or "") == "slider"


def test_apply_named_order_ignores_stale_names_and_collapses_duplicates() -> None:
    ordered = apply_named_order(
        base_names=["alpha", "beta", "gamma"],
        override_names=["gamma", "missing", "gamma", "alpha"],
    )

    assert ordered == ["gamma", "alpha", "beta"]


def test_set_list_order_override_removes_identity_order() -> None:
    node = _TestNode(F8ServiceSpec(serviceClass="f8.test", label="Test"))

    set_list_order_override(
        node,
        key="stateFields",
        order=["first", "second"],
        base_order=["first", "second"],
        rebuild=False,
    )

    assert node.ui_overrides() == {}


def test_rename_and_remove_list_order_entry_preserve_slots_and_cleanup() -> None:
    node = _TestNode(F8ServiceSpec(serviceClass="f8.test", label="Test"))
    node.set_ui_overrides({"listOrder": {"stateFields": ["second", "first"]}}, rebuild=False)

    rename_list_order_entry(
        node,
        key="stateFields",
        old_name="second",
        new_name="renamed",
        base_order=["first", "renamed"],
        rebuild=False,
    )
    assert node.ui_overrides() == {"listOrder": {"stateFields": ["renamed", "first"]}}

    remove_list_order_entry(
        node,
        key="stateFields",
        entry_name="renamed",
        base_order=["first"],
        rebuild=False,
    )
    assert node.ui_overrides() == {}

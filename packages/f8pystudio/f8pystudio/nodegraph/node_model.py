from __future__ import annotations
from f8pysdk.msgspec_codec import dump_json, validate_as
import json
import logging

import msgspec

from NodeGraphQt.base.model import NodeModel

from f8pysdk import F8OperatorSpec, F8ServiceSpec, F8VariantRef
from f8pysdk.spec_metadata import coerce_spec_payload
from ..variants.variant_metadata import normalize_variant_sys_metadata, variant_ref_from_dict, variant_ref_to_json


logger = logging.getLogger(__name__)


class F8StudioNodeModel(NodeModel):
    """
    Studio node model that persists framework system data in the NodeGraphQt
    session via default node model fields (not custom properties).
    """

    f8_spec: F8OperatorSpec | F8ServiceSpec | None
    f8_sys: dict[str, object]
    f8_ui_overrides: dict[str, object]
    f8_ui_state: dict[str, object]

    def __init__(self):
        super().__init__()
        self.f8_spec = None
        self.f8_sys = {}
        self.f8_ui_overrides = {}
        self.f8_ui_state = {}
        self._owner_node: object | None = None

    @staticmethod
    def _coerce_spec(value: object) -> F8OperatorSpec | F8ServiceSpec | None:
        if value is None:
            return None
        return coerce_spec_payload(value)

    def set_property(self, name, value):
        if name == "f8_spec":
            old = self.f8_spec
            self.f8_spec = self._coerce_spec(value)
            # Important: during node construction, the template spec is set once.
            # Subclasses build ports/properties in their __init__. We must NOT
            # trigger sync_from_spec() on this first assignment, otherwise ports
            # may be registered twice (causing PortRegistrationError).
            if old is not None and self.f8_spec is not None:
                owner = self._owner_node
                if owner is not None:
                    try:
                        owner.sync_from_spec()  # type: ignore[attr-defined]
                    except Exception:
                        logger.exception("Failed to sync node after f8_spec update.")
            self._emit_owner_property_changed("f8_spec", self.f8_spec)
            return
        if name == "f8_ui_overrides":
            if isinstance(value, dict):
                self.f8_ui_overrides = value
            elif value is None:
                self.f8_ui_overrides = {}
            else:
                raise TypeError(f"Unsupported `f8_ui_overrides` type: {type(value)!r}")
            # UI changes affect effective state fields/ports, so resync.
            if self.f8_spec is not None:
                owner = self._owner_node
                if owner is not None:
                    try:
                        owner.sync_from_spec()  # type: ignore[attr-defined]
                    except Exception:
                        logger.exception("Failed to sync node after f8_ui_overrides update.")
            self._emit_owner_property_changed("f8_ui_overrides", self.f8_ui_overrides)
            return
        if name == "f8_ui_state":
            if isinstance(value, dict):
                self.f8_ui_state = value
            elif value is None:
                self.f8_ui_state = {}
            else:
                raise TypeError(f"Unsupported `f8_ui_state` type: {type(value)!r}")
            self._emit_owner_property_changed("f8_ui_state", self.f8_ui_state)
            return
        if name == "f8_sys":
            if isinstance(value, dict):
                self.f8_sys = normalize_variant_sys_metadata(value)
            elif value is None:
                self.f8_sys = {}
            else:
                raise TypeError(f"Unsupported `f8_sys` type: {type(value)!r}")
            self._emit_owner_property_changed("f8_sys", self.f8_sys)
            return
        if name == "nodePurpose":
            self.nodePurpose = "" if value is None else str(value)
            return
        return super().set_property(name, value)

    def _emit_owner_property_changed(self, name: str, value: object) -> None:
        owner = self._owner_node
        if owner is None:
            return
        try:
            graph = owner.graph  # type: ignore[attr-defined]
        except AttributeError:
            return
        if graph is None:
            return
        try:
            signal = graph.property_changed  # type: ignore[attr-defined]
        except AttributeError:
            return
        if signal is None:
            return
        try:
            signal.emit(owner, str(name or ""), value)
        except Exception:
            logger.exception("Failed to emit property_changed for system property: %s", str(name or ""))

    @property
    def to_dict(self):
        """
        Override serialization to:
          1) omit port restore definitions (ports are derived from spec)
          2) serialize structured spec to plain JSON dict
        """
        data = super().to_dict
        ((node_id, node_dict),) = data.items()

        # Never persist runtime-only helpers into session JSON.
        node_dict.pop("_owner_node", None)

        spec = node_dict.get("f8_spec")
        if isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
            node_dict["f8_spec"] = dump_json(spec, mode="json")
        if isinstance(self.f8_sys, dict) and self.f8_sys:
            node_dict["f8_sys"] = normalize_variant_sys_metadata(self.f8_sys)

        if isinstance(self.f8_ui_overrides, dict) and self.f8_ui_overrides:
            node_dict["f8_ui_overrides"] = self.f8_ui_overrides
        if isinstance(self.f8_ui_state, dict) and self.f8_ui_state:
            node_dict["f8_ui_state"] = self.f8_ui_state

        safe_node_dict = self._json_safe_value(node_dict, seen=set())
        if not isinstance(safe_node_dict, dict):
            safe_node_dict = {}
        return {node_id: safe_node_dict}

    @property
    def serial(self):
        """
        Serialize model information to a string.

        Returns:
            str: serialized JSON string.
        """
        model_dict = self.to_dict

        # We never want NodeGraphQt to restore port definitions from session.
        model_dict[self.id].pop("port_deletion_allowed", None)
        model_dict[self.id].pop("input_ports", None)
        model_dict[self.id].pop("output_ports", None)
        return json.dumps(model_dict)

    @classmethod
    def _json_safe_value(cls, value: object, *, seen: set[int]) -> object:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, msgspec.UnsetType):
            return None
        if isinstance(value, dict):
            value_id = id(value)
            if value_id in seen:
                return None
            seen.add(value_id)
            out: dict[str, object] = {}
            for key, item in value.items():
                out[str(key)] = cls._json_safe_value(item, seen=seen)
            seen.discard(value_id)
            return out
        if isinstance(value, (list, tuple, set)):
            value_id = id(value)
            if value_id in seen:
                return None
            seen.add(value_id)
            out_list: list[object] = []
            for item in value:
                out_list.append(cls._json_safe_value(item, seen=seen))
            seen.discard(value_id)
            return out_list
        try:
            dumped = dump_json(value, mode="json")
        except (AttributeError, TypeError, ValueError):
            return str(value)
        if dumped is value:
            return str(value)
        return cls._json_safe_value(dumped, seen=seen)

    @property
    def svcId(self) -> object | None:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        return self.f8_sys.get("svcId")

    @svcId.setter
    def svcId(self, value: object | None) -> None:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        if value is None:
            self.f8_sys.pop("svcId", None)
        else:
            self.f8_sys["svcId"] = value

    @property
    def missingLocked(self) -> bool:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        return bool(self.f8_sys.get("missingLocked"))

    @missingLocked.setter
    def missingLocked(self, value: bool) -> None:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        self.f8_sys["missingLocked"] = bool(value)

    @property
    def missingType(self) -> str:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        return str(self.f8_sys.get("missingType") or "").strip()

    @missingType.setter
    def missingType(self, value: str) -> None:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        self.f8_sys["missingType"] = str(value or "").strip()

    @property
    def missingReason(self) -> str:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        return str(self.f8_sys.get("missingReason") or "").strip()

    @missingReason.setter
    def missingReason(self, value: str) -> None:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        self.f8_sys["missingReason"] = str(value or "").strip()

    @property
    def missingRendererFallback(self) -> bool:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        return bool(self.f8_sys.get("missingRendererFallback"))

    @missingRendererFallback.setter
    def missingRendererFallback(self, value: bool) -> None:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        self.f8_sys["missingRendererFallback"] = bool(value)

    @property
    def nodePurpose(self) -> str:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        return str(self.f8_sys.get("nodePurpose") or "").strip()

    @nodePurpose.setter
    def nodePurpose(self, value: str) -> None:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        self.f8_sys["nodePurpose"] = str(value or "").strip()

    @property
    def variantRef(self) -> F8VariantRef | None:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        raw = self.f8_sys.get("variantRef")
        if not isinstance(raw, dict):
            return None
        try:
            return variant_ref_from_dict(raw)
        except Exception:
            return None

    @variantRef.setter
    def variantRef(self, value: F8VariantRef | None) -> None:
        if not isinstance(self.f8_sys, dict):
            self.f8_sys = {}
        if value is None:
            self.f8_sys.pop("variantRef", None)
            return
        self.f8_sys["variantRef"] = variant_ref_to_json(value)

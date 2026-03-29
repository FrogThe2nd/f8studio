from __future__ import annotations

from f8pysdk import F8OperatorSpec
from f8pysdk.msgspec_codec import dump_json

from ..nodegraph.operator_basenode import F8StudioOperatorBaseNode
from ..nodegraph.patch_hub_nodeitem import F8StudioPatchHubNodeItem
from ..operators.patch_hub import PatchHubRuntimeNode, normalize_patch_hub_spec


class PatchHubRenderNode(F8StudioOperatorBaseNode):
    SPEC_TEMPLATE = PatchHubRuntimeNode.SPEC

    def __init__(self) -> None:
        super().__init__(qgraphics_item=F8StudioPatchHubNodeItem)

    def _build_state_properties(self) -> None:  # type: ignore[override]
        return

    def sync_from_spec(self) -> None:
        spec = self.spec
        if isinstance(spec, F8OperatorSpec):
            normalized = normalize_patch_hub_spec(spec)
            if dump_json(normalized, mode="json") != dump_json(spec, mode="json"):
                self.set_spec(normalized, rebuild=False)
                spec = normalized

        for field in list(spec.stateFields or []):
            field_name = str(field.name or "").strip()
            if not field_name:
                continue
            self.model.custom_properties.pop(field_name, None)
            self.model.properties.pop(field_name, None)

        super().sync_from_spec()

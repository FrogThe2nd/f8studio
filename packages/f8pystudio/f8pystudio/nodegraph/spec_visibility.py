from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from f8pysdk.specs import F8OperatorSpec, F8ServiceSpec


SpecTemplate: TypeAlias = F8OperatorSpec | F8ServiceSpec


@runtime_checkable
class SupportsSpecTemplate(Protocol):
    SPEC_TEMPLATE: object


def typed_spec_template_or_none(node_cls: object) -> SpecTemplate | None:
    if not isinstance(node_cls, type):
        return None
    if not isinstance(node_cls, SupportsSpecTemplate):
        return None
    spec = node_cls.SPEC_TEMPLATE
    if not isinstance(spec, (F8OperatorSpec, F8ServiceSpec)):
        return None
    return spec


def is_hidden_spec_node_class(node_cls: object) -> bool:
    """
    Return True when a node class has `SPEC_TEMPLATE.hiddenInPalette` set.
    """
    spec = typed_spec_template_or_none(node_cls)
    if spec is None:
        return False
    return bool(spec.hiddenInPalette)

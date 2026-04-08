from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from f8pysdk.registry import RuntimeNodeRegistry


@dataclass(frozen=True)
class PluginRendererRegistration:
    renderer_class: str
    node_class: type[Any]


@dataclass(frozen=True)
class PluginOperatorRegistration:
    register: Callable[[RuntimeNodeRegistry], RuntimeNodeRegistry]


@dataclass(frozen=True)
class StudioPluginManifest:
    plugin_id: str
    plugin_name: str
    plugin_version: str
    renderers: tuple[PluginRendererRegistration, ...] = field(default_factory=tuple)
    operators: tuple[PluginOperatorRegistration, ...] = field(default_factory=tuple)


@runtime_checkable
class StudioPlugin(Protocol):
    def manifest(self) -> StudioPluginManifest:
        """
        Return plugin metadata and renderer registrations.
        """

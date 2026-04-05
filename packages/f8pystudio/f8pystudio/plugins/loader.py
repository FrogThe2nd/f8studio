from __future__ import annotations

import importlib.metadata
import logging
from dataclasses import dataclass

from f8pystudio.plugins.api import StudioPlugin, StudioPluginManifest

logger = logging.getLogger(__name__)

PLUGIN_ENTRYPOINT_GROUP = "f8studio.pystudio.plugins"


@dataclass(frozen=True)
class _LoadedManifest:
    entry_name: str
    entry_value: str
    manifest: StudioPluginManifest


def _iter_group_entrypoints(*, group: str) -> list[importlib.metadata.EntryPoint]:
    all_eps = importlib.metadata.entry_points()
    if hasattr(all_eps, "select"):
        selected = list(all_eps.select(group=group))
    elif isinstance(all_eps, dict):
        selected = list(all_eps.get(group, ()))
    else:
        selected = []
    return sorted(selected, key=lambda ep: (str(ep.name), str(ep.value)))


def _coerce_manifest(obj: object) -> StudioPluginManifest:
    if isinstance(obj, StudioPluginManifest):
        return obj
    if isinstance(obj, StudioPlugin):
        return obj.manifest()
    manifest_attr = getattr(obj, "manifest", None)
    if callable(manifest_attr):
        out = manifest_attr()
        if isinstance(out, StudioPluginManifest):
            return out
    raise TypeError("entrypoint object must be StudioPluginManifest or StudioPlugin")


def load_entrypoint_plugins(*, group: str = PLUGIN_ENTRYPOINT_GROUP) -> list[StudioPluginManifest]:
    """
    Load plugin manifests via python entry points.

    Failures are isolated per plugin entrypoint and won't abort startup.
    """
    loaded: list[_LoadedManifest] = []
    for entry in _iter_group_entrypoints(group=group):
        entry_name = str(entry.name or "").strip()
        entry_value = str(entry.value or "").strip()
        try:
            raw = entry.load()
            manifest = _coerce_manifest(raw)
            plugin_id = str(manifest.plugin_id or "").strip()
            if not plugin_id:
                raise ValueError("plugin_id is empty")
            loaded.append(_LoadedManifest(entry_name=entry_name, entry_value=entry_value, manifest=manifest))
        except Exception:
            logger.exception(
                "Plugin load failed group=%s entry=%s target=%s",
                group,
                entry_name,
                entry_value,
            )

    # deterministic ordering + duplicate plugin id de-dupe.
    unique_by_id: dict[str, StudioPluginManifest] = {}
    for item in sorted(loaded, key=lambda x: (str(x.manifest.plugin_id), x.entry_name, x.entry_value)):
        pid = str(item.manifest.plugin_id)
        if pid in unique_by_id:
            logger.warning(
                "Duplicate plugin_id '%s' from entry '%s' ignored; first registration wins.",
                pid,
                item.entry_name,
            )
            continue
        unique_by_id[pid] = item.manifest
    return list(unique_by_id.values())

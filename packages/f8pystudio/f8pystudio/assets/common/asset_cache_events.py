from __future__ import annotations

from collections.abc import Callable

from ..components.component_events import subscribe_components_changed
from ..variants.variant_events import subscribe_variants_changed


def subscribe_asset_cache_changed(callback: Callable[[], None]) -> Callable[[], None]:
    unsubscribe_components_changed = subscribe_components_changed(callback)
    unsubscribe_variants_changed = subscribe_variants_changed(callback)

    def unsubscribe() -> None:
        unsubscribe_components_changed()
        unsubscribe_variants_changed()

    return unsubscribe

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StatePublishOptions:
    """
    Explicit controls for state publish side-effects.

    These options are internal runtime controls. Metadata passed via `meta`
    should describe the write for diagnostics/validation/persistence, not steer
    propagation behavior.
    """

    fanout_intra_state_edges: bool = True


__all__ = ["StatePublishOptions"]

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


CrossPublishDecision: TypeAlias = Literal["publish", "suppressed", "local_only"]


@dataclass(frozen=True)
class CrossPublishPlan:
    """
    Explicit cross-service publish evaluation for one emitted sample.
    """

    subject: str
    decision: CrossPublishDecision

    @property
    def will_publish(self) -> bool:
        return self.decision == "publish" and bool(self.subject)


@dataclass(frozen=True)
class DataEmitOptions:
    """
    Explicit controls for routing one emitted data sample.

    These options are internal router controls. They describe whether a sample
    should fan out to local inputs and whether it may be published on the
    cross-service data subjects selected by the current rungraph/policy.
    """

    deliver_local: bool = True
    publish_cross_service: bool = True

    @classmethod
    def local_compute_only(cls) -> "DataEmitOptions":
        """
        Local pull-triggered compute should satisfy local consumers only.
        """
        return cls(deliver_local=True, publish_cross_service=False)

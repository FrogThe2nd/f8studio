from __future__ import annotations

from typing import Literal, TypeAlias


CrossPublishPolicy: TypeAlias = Literal["routed", "all", "none"]
DataDeliveryMode: TypeAlias = Literal["buffered", "callback"]

__all__ = ["CrossPublishPolicy", "DataDeliveryMode"]

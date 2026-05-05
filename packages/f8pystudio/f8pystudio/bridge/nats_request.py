from __future__ import annotations

from .runtime_request import RuntimeRequester, request_typed


NatsRequester = RuntimeRequester

__all__ = ["NatsRequester", "request_typed"]

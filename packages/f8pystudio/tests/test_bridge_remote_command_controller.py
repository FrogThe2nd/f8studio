from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from f8pystudio.bridge.remote_command_controller import RemoteCommandControllerMixin


@dataclass
class _CommandResponse:
    ok: bool
    result: dict[str, Any]
    error_message: str | None = None


class _CommandGateway:
    def __init__(self, *, response: _CommandResponse | None = None, error: BaseException | None = None) -> None:
        self._response = response or _CommandResponse(ok=True, result={})
        self._error = error
        self.calls: list[Any] = []

    async def request_command(self, request: Any) -> _CommandResponse:
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        return self._response


class _Harness(RemoteCommandControllerMixin):
    def __init__(
        self,
        *,
        gateway: _CommandGateway | None = None,
        submit_ok: bool = True,
    ) -> None:
        self.studio_service_id = "studio_default"
        self._pending_remote_command_cbs: dict[str, Any] = {}
        self._command_gateway = gateway or _CommandGateway()
        self._submit_ok = bool(submit_ok)
        self._submitted: list[Any] = []
        self.logs: list[str] = []
        self.exceptions: list[str] = []
        self.emitted_responses: list[tuple[str, object, object]] = []

    def _submit_async(self, coro: Any, *, context: str) -> bool:
        _ = context
        if not self._submit_ok:
            if asyncio.iscoroutine(coro):
                coro.close()
            return False
        self._submitted.append(coro)
        return True

    def _emit_remote_command_response_safe(self, req_id: str, result: object, err: object) -> None:
        self.emitted_responses.append((str(req_id), result, err))
        self._on_remote_command_response(str(req_id), result, err)

    def _report_exception(self, context: str, exc: BaseException) -> None:
        self.exceptions.append(f"{context}:{type(exc).__name__}")

    def _emit_log_line(self, line: str) -> None:
        self.logs.append(str(line))


def test_request_remote_command_success_delivers_callback() -> None:
    bridge = _Harness(gateway=_CommandGateway(response=_CommandResponse(ok=True, result={"ok": 1})))
    callback_calls: list[tuple[dict[str, Any] | None, str | None]] = []

    bridge.request_remote_command(
        service_id="svc_alpha",
        call="ping",
        args={"x": 1},
        cb=lambda result, err: callback_calls.append((result, err)),
    )

    assert len(bridge._submitted) == 1
    asyncio.run(bridge._submitted[0])

    assert len(bridge._command_gateway.calls) == 1
    assert callback_calls == [({"ok": 1}, None)]
    assert bridge.exceptions == []


def test_request_remote_command_invalid_service_id_fails_fast() -> None:
    bridge = _Harness()
    callback_calls: list[tuple[dict[str, Any] | None, str | None]] = []

    bridge.request_remote_command(
        service_id="bad.service.id",
        call="ping",
        args={},
        cb=lambda result, err: callback_calls.append((result, err)),
    )

    assert bridge._submitted == []
    assert len(callback_calls) == 1
    assert callback_calls[0][0] is None
    assert callback_calls[0][1] is not None


def test_request_remote_command_submit_failure_returns_error() -> None:
    bridge = _Harness(submit_ok=False)
    callback_calls: list[tuple[dict[str, Any] | None, str | None]] = []

    bridge.request_remote_command(
        service_id="svc_beta",
        call="ping",
        args={},
        cb=lambda result, err: callback_calls.append((result, err)),
    )

    assert callback_calls == [(None, "submit failed")]


def test_invoke_remote_command_logs_rejection() -> None:
    bridge = _Harness(gateway=_CommandGateway(response=_CommandResponse(ok=False, result={}, error_message="denied")))

    bridge.invoke_remote_command(service_id="svc_gamma", call="doWork", args={"a": 1})

    assert len(bridge._submitted) == 1
    asyncio.run(bridge._submitted[0])

    assert len(bridge._command_gateway.calls) == 1
    assert any("failed" in line and "denied" in line for line in bridge.logs)

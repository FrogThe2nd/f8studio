from __future__ import annotations

import concurrent.futures
from typing import Any, Callable

from qtpy import QtCore

from f8pysdk.f8_naming import ensure_token, new_id

from .command_client import CommandRequest

_REMOTE_COMMAND_CALLBACK_ERRORS = (Exception,)
_REMOTE_COMMAND_REQUEST_ERRORS = (Exception,)
_REMOTE_COMMAND_WAIT_ERRORS = (
    concurrent.futures.CancelledError,
    RuntimeError,
    TypeError,
    ValueError,
)


class RemoteCommandControllerMixin:
    def _on_remote_command_response(self, req_id: str, result: object, err: object) -> None:
        cb = self._pending_remote_command_cbs.pop(str(req_id), None)
        if cb is None:
            return
        try:
            cb(result if isinstance(result, dict) else None, str(err) if err else None)
        except _REMOTE_COMMAND_CALLBACK_ERRORS as exc:
            self._report_exception("remote command response callback failed", exc)

    def request_remote_command(
        self,
        service_id: str,
        call: str,
        args: Any,
        cb: Callable[[dict[str, Any] | None, str | None], None],
        *,
        timeout_s: float = 2.0,
    ) -> None:
        """
        Invoke a user-defined command on a remote service and return the parsed `result`.

        Declared commands may use scalar/list/object args; transport normalization
        happens service-side from the command spec.

        Callback is always delivered on the Qt main thread as:
        - cb(result_dict, None) on success (result may be any JSON object; non-dict becomes {"value": ...})
        - cb(None, "error message") on failure
        """
        req_id = new_id()
        self._pending_remote_command_cbs[str(req_id)] = cb

        try:
            service_id = ensure_token(str(service_id), label="service_id")
        except ValueError as exc:
            self._emit_remote_command_response_safe(str(req_id), None, str(exc))
            return
        call = str(call or "").strip()
        if not call or service_id == self.studio_service_id:
            self._emit_remote_command_response_safe(str(req_id), None, "invalid call/service_id")
            return

        async def _do() -> None:
            try:
                response = await self._command_gateway.request_command(
                    CommandRequest(
                        service_id=service_id,
                        call=call,
                        args=args,
                        timeout_s=float(timeout_s),
                        source="ui",
                        actor="studio",
                    )
                )
                if response.ok:
                    self._emit_remote_command_response_safe(str(req_id), dict(response.result), None)
                    return
                self._emit_remote_command_response_safe(str(req_id), None, str(response.error_message or "rejected"))
            except _REMOTE_COMMAND_REQUEST_ERRORS as exc:
                self._emit_remote_command_response_safe(str(req_id), None, f"{type(exc).__name__}: {exc}")

        submitted = self._submit_async(_do(), context=f"submit request_remote_command failed serviceId={service_id}")
        if not submitted:
            self._emit_remote_command_response_safe(str(req_id), None, "submit failed")

    def invoke_remote_command(self, service_id: str, call: str, args: Any = None) -> None:
        """
        Invoke a user-defined command on a remote service via the reserved cmd channel.

        Request is sent to `cmd_channel_key(service_id)` with a JSON envelope
        (reqId/call/args/meta). This matches the service control plane `cmd` endpoint.
        Declared commands may use scalar/list/object args.
        """
        try:
            service_id = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        call = str(call or "").strip()
        if not call or service_id == self.studio_service_id:
            return

        async def _do() -> None:
            try:
                response = await self._command_gateway.request_command(
                    CommandRequest(
                        service_id=service_id,
                        call=call,
                        args=args,
                        timeout_s=1.5,
                        source="ui",
                        actor="studio",
                    )
                )
                if response.ok:
                    return
                self._emit_log_line(f"command {call} failed serviceId={service_id}: {response.error_message or 'rejected'}")
            except _REMOTE_COMMAND_REQUEST_ERRORS as exc:
                self._emit_log_line(f"command {call} failed serviceId={service_id}: {type(exc).__name__}: {exc}")

        self._submit_async(_do(), context=f"submit invoke_remote_command failed serviceId={service_id}")

    def invoke_remote_command_and_wait(
        self,
        service_id: str,
        call: str,
        args: Any = None,
        *,
        timeout_s: float = 2.0,
    ) -> dict[str, Any]:
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError as exc:
            return {"submitted": False, "completed": False, "ok": False, "result": {}, "error": str(exc)}
        call_name = str(call or "").strip()
        if not call_name or sid == self.studio_service_id:
            return {
                "submitted": False,
                "completed": False,
                "ok": False,
                "result": {},
                "error": "invalid call/service_id",
            }

        async def _do() -> dict[str, Any]:
            response = await self._command_gateway.request_command(
                CommandRequest(
                    service_id=sid,
                    call=call_name,
                    args=args,
                    timeout_s=float(timeout_s),
                    source="automation",
                    actor="studio",
                )
            )
            payload_obj = response.payload if isinstance(response.payload, dict) else {}
            return {
                "ok": bool(response.ok),
                "result": dict(response.result),
                "error": "" if response.ok else str(response.error_message or "rejected"),
                "payload": dict(payload_obj),
            }

        future = self._submit_async_future(
            _do(),
            context=f"submit invoke_remote_command_and_wait failed serviceId={sid}",
        )
        if future is None:
            return {"submitted": False, "completed": False, "ok": False, "result": {}, "error": "submit failed"}
        try:
            result = future.result(timeout=float(timeout_s) + 0.25)
        except concurrent.futures.TimeoutError:
            self._emit_log_line(f"command {call_name} timed out serviceId={sid}")
            return {"submitted": True, "completed": False, "ok": False, "result": {}, "error": "timeout"}
        except _REMOTE_COMMAND_WAIT_ERRORS as exc:
            self._report_exception(f"invoke_remote_command_and_wait failed serviceId={sid}", exc)
            return {
                "submitted": True,
                "completed": True,
                "ok": False,
                "result": {},
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "submitted": True,
            "completed": True,
            "ok": bool(result.get("ok", False)),
            "result": dict(result.get("result") if isinstance(result.get("result"), dict) else {}),
            "error": str(result.get("error") or ""),
            "payload": dict(result.get("payload") if isinstance(result.get("payload"), dict) else {}),
        }

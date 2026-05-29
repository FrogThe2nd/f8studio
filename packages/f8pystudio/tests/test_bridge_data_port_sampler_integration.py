from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from typing import Any

from f8pysdk.codec import encode_obj
from f8pysdk.f8_naming import data_key
from f8pysdk.testing import InMemoryCluster, InMemoryTransport
from f8pystudio.bridge.deploy_state_controller import DeployStateControllerMixin


class _Harness(DeployStateControllerMixin):
    def __init__(self) -> None:
        self._cluster = InMemoryCluster()
        self._transport = InMemoryTransport(cluster=self._cluster)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.logs: list[str] = []
        self.exceptions: list[str] = []

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _ensure_runtime_transport(self) -> InMemoryTransport:
        return self._transport

    def _submit_async_future(self, coro: Any, *, context: str) -> concurrent.futures.Future[Any] | None:
        _ = context
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _wait_for_submitted_future(
        self,
        future: concurrent.futures.Future[Any],
        *,
        timeout_s: float,
        context: str,
        timeout_message: str,
    ) -> dict[str, Any]:
        _ = context
        try:
            return {"completed": True, "result": future.result(timeout=float(timeout_s)), "error": ""}
        except concurrent.futures.TimeoutError:
            self.logs.append(str(timeout_message))
            return {"completed": False, "result": None, "error": "timeout"}

    def _emit_log_line(self, line: str) -> None:
        self.logs.append(str(line))

    def _report_exception(self, context: str, exc: BaseException) -> None:
        self.exceptions.append(f"{context}:{type(exc).__name__}")

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=1.0)
        self._loop.close()


def test_sample_data_port_and_wait_receives_in_memory_sample() -> None:
    harness = _Harness()
    try:
        key = data_key("svc", from_node_id="node", port_id="out")

        async def publish_later() -> None:
            await asyncio.sleep(0.05)
            await harness._cluster.publish(key, encode_obj({"value": {"ok": True}, "ts": 123}))

        asyncio.run_coroutine_threadsafe(publish_later(), harness._loop)

        result = harness.sample_data_port_and_wait(
            "svc",
            "node",
            "out",
            limit=1,
            timeout_s=1.0,
            include_value=True,
        )
    finally:
        harness.close()

    assert result["submitted"] is True
    assert result["completed"] is True
    assert result["timedOut"] is False
    assert result["error"] == ""
    assert result["samples"][0]["value"] == {"ok": True}
    assert result["samples"][0]["tsMs"] == 123

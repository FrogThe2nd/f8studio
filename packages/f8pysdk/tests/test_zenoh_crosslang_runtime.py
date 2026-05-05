from __future__ import annotations

import asyncio
import json
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from f8pysdk.nats_naming import kv_bucket_for_service, kv_key_node_state, svc_endpoint_subject
from f8pysdk.zenoh_transport import ZenohTransport, ZenohTransportConfig


ROOT = Path(__file__).resolve().parents[3]
PROBE_BIN = ROOT / "build" / "Release" / "bin" / "f8cpp_crosslang_runtime_probe"


def _sid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _probe_command(*args: str) -> list[str]:
    return ["pixi", "run", "-e", "cpp", str(PROBE_BIN), *args]


def _run_probe_capture(*args: str, timeout_s: float = 8.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _probe_command(*args),
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=float(timeout_s),
        check=False,
    )


def _require_probe_binary() -> None:
    if not PROBE_BIN.exists():
        pytest.skip(f"C++ runtime probe is not built: {PROBE_BIN}")


def _wait_for_ready_file(path: Path, proc: subprocess.Popen[str], timeout_s: float) -> None:
    deadline = time.monotonic() + float(timeout_s)
    while time.monotonic() < deadline:
        if path.exists():
            return
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=1.0)
            raise RuntimeError(
                f"C++ probe exited before ready file was created rc={proc.returncode} "
                f"stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.01)
    raise TimeoutError(f"C++ probe did not create ready file within {timeout_s:g}s: {path}")


async def _wait_until(predicate: Callable[[], bool], *, timeout_s: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + float(timeout_s)
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("condition was not satisfied")


def _parse_probe_json(stdout: str) -> dict[str, object]:
    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"probe stdout did not contain a JSON object: {stdout!r}")


def test_python_client_talks_to_cpp_command_server_and_reads_cpp_retained_state(tmp_path: Path) -> None:
    pytest.importorskip("zenoh")
    _require_probe_binary()

    async def _run() -> None:
        service_id = _sid("cpp_srv")
        client_id = _sid("py_cli")
        state_key = kv_key_node_state(node_id="node", field="value")
        state_payload = "cpp-retained-state"
        ready_file = tmp_path / "cpp-server.ready"
        proc = subprocess.Popen(
            _probe_command(
                "--mode",
                "server",
                "--service-id",
                service_id,
                "--state-key",
                state_key,
                "--state-payload",
                state_payload,
                "--ready-file",
                str(ready_file),
                "--duration-ms",
                "10000",
            ),
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        client = ZenohTransport(ZenohTransportConfig(service_id=client_id))
        await client.connect()
        try:
            _wait_for_ready_file(ready_file, proc, timeout_s=5.0)

            seen: list[tuple[str, bytes]] = []

            async def _on_state(key: str, value: bytes) -> None:
                seen.append((key, value))

            watch = await client.kv_watch_in_bucket(kv_bucket_for_service(service_id), state_key, cb=_on_state)
            try:
                await _wait_until(lambda: bool(seen), timeout_s=5.0)
                assert seen[0] == (state_key, state_payload.encode())
            finally:
                await watch.unsubscribe()

            response = await client.request(
                svc_endpoint_subject(service_id, "echo"),
                b"from-python",
                timeout=3.0,
                raise_on_error=True,
            )
            assert response == b"cpp:from-python"

            terminate_response = await client.request(
                svc_endpoint_subject(service_id, "terminate"),
                b"stop",
                timeout=3.0,
                raise_on_error=True,
            )
            assert terminate_response == b"bye:stop"
            stdout, stderr = proc.communicate(timeout=5.0)
            assert proc.returncode == 0, stderr
            parsed = _parse_probe_json(stdout)
            assert parsed.get("terminated") is True
        finally:
            await client.close()
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.communicate(timeout=5.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate(timeout=5.0)

    asyncio.run(_run())


def test_cpp_client_talks_to_python_command_server() -> None:
    pytest.importorskip("zenoh")
    _require_probe_binary()

    async def _run() -> None:
        server_id = _sid("py_srv")
        client_id = _sid("cpp_cli")
        server = ZenohTransport(ZenohTransportConfig(service_id=server_id))
        await server.connect()
        try:
            async def _handler(payload: bytes) -> bytes:
                return b"py:" + bytes(payload)

            handle = await server.serve(svc_endpoint_subject(server_id, "echo"), _handler)
            try:
                result = await asyncio.to_thread(
                    _run_probe_capture,
                    "--mode",
                    "request",
                    "--service-id",
                    client_id,
                    "--target-service-id",
                    server_id,
                    "--endpoint",
                    "echo",
                    "--payload",
                    "from-cpp",
                    "--timeout-ms",
                    "3000",
                )
                assert result.returncode == 0, result.stderr
                parsed = _parse_probe_json(result.stdout)
                assert parsed == {"mode": "request", "ok": True, "response": "py:from-cpp"}
            finally:
                await handle.unsubscribe()
        finally:
            await server.close()

    asyncio.run(_run())


def test_cpp_late_subscriber_reads_python_retained_state() -> None:
    pytest.importorskip("zenoh")
    _require_probe_binary()

    async def _run() -> None:
        publisher_id = _sid("py_state")
        watcher_id = _sid("cpp_watch")
        state_key = kv_key_node_state(node_id="node", field="value")
        state_payload = "python-retained-state"
        publisher = ZenohTransport(ZenohTransportConfig(service_id=publisher_id))
        await publisher.connect()
        try:
            await publisher.kv_put(state_key, state_payload.encode())
            result = await asyncio.to_thread(
                _run_probe_capture,
                "--mode",
                "watch",
                "--service-id",
                watcher_id,
                "--peer-service-id",
                publisher_id,
                "--state-key",
                state_key,
                "--expected-payload",
                state_payload,
                "--timeout-ms",
                "5000",
            )
            assert result.returncode == 0, result.stderr
            parsed = _parse_probe_json(result.stdout)
            assert parsed == {"mode": "watch", "ok": True, "key": state_key, "payload": state_payload}
        finally:
            await publisher.close()

    asyncio.run(_run())

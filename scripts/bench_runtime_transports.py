from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
PKG_SDK = ROOT / "packages" / "f8pysdk"
if str(PKG_SDK) not in sys.path:
    sys.path.insert(0, str(PKG_SDK))

import nats  # type: ignore[import-not-found]  # noqa: E402
from nats.js.api import StorageType  # type: ignore[import-not-found]  # noqa: E402
from nats.micro import ServiceConfig, add_service  # type: ignore[import-not-found]  # noqa: E402

from f8pysdk.nats_naming import kv_bucket_for_service, kv_key_node_state, svc_micro_name  # noqa: E402
from f8pysdk.runtime_transport import RuntimeTransport  # noqa: E402
from f8pysdk.shm import VIDEO_FORMAT_BGRA32  # noqa: E402
from f8pysdk.transport import NatsTransport, NatsTransportConfig  # noqa: E402
from f8pysdk.video_transport import (  # noqa: E402
    LegacyShmLatestVideoFrameTransport,
    ZenohLatestVideoFrameTransport,
)
from f8pysdk.zenoh_naming import zenoh_service_liveliness_key  # noqa: E402
from f8pysdk.zenoh_transport import ZenohTransport, ZenohTransportConfig  # noqa: E402

_MSG_HEADER = struct.Struct("<iq")
_FRAME_TS_HEADER = struct.Struct("<q")

Backend = Literal["nats_core", "nats_micro", "nats_js_kv_memory", "nats_js_kv_file", "zenoh", "legacy_shm"]


@dataclass(frozen=True)
class BenchStats:
    name: str
    backend: Backend
    category: str
    payload_bytes: int
    iterations: int
    delivered: int
    lost: int
    elapsed_s: float
    publish_elapsed_s: float
    throughput_ops_s: float
    publish_throughput_ops_s: float
    publish_throughput_mib_s: float
    throughput_mib_s: float
    latency_avg_ms: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    latency_p99_ms: float | None
    min_ms: float | None
    max_ms: float | None
    ok: bool
    note: str = ""


@dataclass(frozen=True)
class BenchRun:
    run_id: str
    timestamp_utc: str
    machine: dict[str, Any]
    nats_url: str
    results: list[BenchStats]


class NatsServer:
    def __init__(self) -> None:
        self.url = ""
        self._proc: subprocess.Popen[bytes] | None = None
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None

    def start(self) -> None:
        binary = shutil.which("nats-server")
        if not binary:
            raise RuntimeError("nats-server is not available on PATH")
        port = _free_tcp_port()
        temp_dir = tempfile.TemporaryDirectory(prefix="f8-bench-nats-")
        store_dir = Path(temp_dir.name) / "js"
        store_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(binary),
            "-js",
            "-a",
            "127.0.0.1",
            "-p",
            str(port),
            "-sd",
            str(store_dir),
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._proc = proc
        self._temp_dir = temp_dir
        self.url = f"nats://127.0.0.1:{port}"
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"nats-server exited early rc={proc.returncode}")
            if _tcp_reachable("127.0.0.1", port):
                return
            time.sleep(0.05)
        raise RuntimeError(f"nats-server did not become reachable on port={port}")

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3.0)
        temp_dir = self._temp_dir
        self._temp_dir = None
        if temp_dir is not None:
            temp_dir.cleanup()


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _tcp_reachable(host: str, port: int) -> bool:
    publish_start = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=0.2):
            return True
    except OSError:
        return False


def _machine_info() -> dict[str, Any]:
    cpu_model = ""
    cpu_count = os.cpu_count()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                _, value = line.split(":", 1)
                cpu_model = value.strip()
                break
    return {
        "platform": platform.platform(),
        "python": sys.version.replace("\n", " "),
        "processor": platform.processor(),
        "cpu_model": cpu_model,
        "cpu_count": cpu_count,
        "pid": os.getpid(),
    }


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (float(pct) / 100.0) * float(len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - float(low)
    return float(sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac)


def _stats(
    *,
    name: str,
    backend: Backend,
    category: str,
    payload_bytes: int,
    iterations: int,
    delivered: int,
    elapsed_s: float,
    publish_elapsed_s: float,
    latencies_ns: list[int],
    ok: bool = True,
    note: str = "",
) -> BenchStats:
    lost = max(0, int(iterations) - int(delivered))
    lat_ms = sorted(float(item) / 1_000_000.0 for item in latencies_ns)
    avg = (sum(lat_ms) / float(len(lat_ms))) if lat_ms else None
    throughput = float(delivered) / float(elapsed_s) if elapsed_s > 0 else 0.0
    publish_throughput = float(iterations) / float(publish_elapsed_s) if publish_elapsed_s > 0 else 0.0
    mib = float(delivered) * float(payload_bytes) / (1024.0 * 1024.0)
    publish_mib = float(iterations) * float(payload_bytes) / (1024.0 * 1024.0)
    return BenchStats(
        name=name,
        backend=backend,
        category=category,
        payload_bytes=int(payload_bytes),
        iterations=int(iterations),
        delivered=int(delivered),
        lost=lost,
        elapsed_s=float(elapsed_s),
        publish_elapsed_s=float(publish_elapsed_s),
        throughput_ops_s=throughput,
        publish_throughput_ops_s=publish_throughput,
        publish_throughput_mib_s=(publish_mib / float(publish_elapsed_s)) if publish_elapsed_s > 0 else 0.0,
        throughput_mib_s=(mib / float(elapsed_s)) if elapsed_s > 0 else 0.0,
        latency_avg_ms=avg,
        latency_p50_ms=_percentile(lat_ms, 50.0),
        latency_p95_ms=_percentile(lat_ms, 95.0),
        latency_p99_ms=_percentile(lat_ms, 99.0),
        min_ms=lat_ms[0] if lat_ms else None,
        max_ms=lat_ms[-1] if lat_ms else None,
        ok=bool(ok),
        note=str(note),
    )


def _payload(size: int, *, seed: int = 0) -> bytes:
    size_i = max(0, int(size))
    if size_i == 0:
        return b""
    return bytes(((seed + i) % 251 for i in range(size_i)))


async def _close_transport(transport: RuntimeTransport) -> None:
    await transport.close()


async def _stop_subscription(handle: Any) -> None:
    if isinstance(handle, tuple):
        if len(handle) == 2:
            managed = handle[0]
            task = handle[1]
            if isinstance(task, asyncio.Task):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await managed.stop()
        return
    await handle.unsubscribe()


def _nats_transport(
    *,
    service_id: str,
    nats_url: str,
    storage: StorageType = StorageType.MEMORY,
    delete_bucket_on_connect: bool = True,
    delete_bucket_on_close: bool = True,
) -> NatsTransport:
    return NatsTransport(
        NatsTransportConfig(
            url=str(nats_url),
            kv_bucket=kv_bucket_for_service(service_id),
            kv_storage=storage,
            delete_bucket_on_connect=delete_bucket_on_connect,
            delete_bucket_on_close=delete_bucket_on_close,
        )
    )


def _zenoh_transport(*, service_id: str, shm_pool_bytes: int) -> ZenohTransport:
    return ZenohTransport(
        ZenohTransportConfig(
            service_id=service_id,
            shm_pool_bytes=int(shm_pool_bytes),
        )
    )


async def bench_pubsub(
    *,
    backend: Literal["nats_core", "zenoh"],
    nats_url: str,
    run_id: str,
    payload_bytes: int,
    iterations: int,
    warmup_iterations: int,
    timeout_s: float,
    zenoh_shm_pool_bytes: int,
) -> BenchStats:
    subject = f"svc.{run_id}.nodes.src.data.pubsub_{payload_bytes}"
    payload_tail = _payload(max(0, int(payload_bytes) - _MSG_HEADER.size), seed=payload_bytes)
    if backend == "nats_core":
        publisher = _nats_transport(service_id=f"{run_id}_pub_{payload_bytes}", nats_url=nats_url)
        subscriber = _nats_transport(service_id=f"{run_id}_sub_{payload_bytes}", nats_url=nats_url)
    else:
        publisher = _zenoh_transport(service_id=f"{run_id}_pub_{payload_bytes}", shm_pool_bytes=zenoh_shm_pool_bytes)
        subscriber = _zenoh_transport(service_id=f"{run_id}_sub_{payload_bytes}", shm_pool_bytes=zenoh_shm_pool_bytes)

    await publisher.connect()
    await subscriber.connect()
    received = 0
    latencies: list[int] = []
    done = asyncio.Event()

    async def _on_message(_subject: str, raw: bytes) -> None:
        nonlocal received
        if len(raw) < _MSG_HEADER.size:
            return
        seq, sent_ns = _MSG_HEADER.unpack_from(raw, 0)
        if seq < 0:
            return
        received += 1
        latencies.append(time.perf_counter_ns() - int(sent_ns))
        if received >= int(iterations):
            done.set()

    sub = await subscriber.subscribe(subject, cb=_on_message)
    await asyncio.sleep(0.05)
    try:
        for warmup_seq in range(max(0, int(warmup_iterations))):
            raw = _MSG_HEADER.pack(-1, time.perf_counter_ns()) + payload_tail
            await publisher.publish(subject, raw)
            if warmup_seq % 256 == 0:
                await asyncio.sleep(0)
        await asyncio.sleep(0.02)
        publish_start = time.perf_counter()
        for seq in range(int(iterations)):
            raw = _MSG_HEADER.pack(seq, time.perf_counter_ns()) + payload_tail
            await publisher.publish(subject, raw)
        publish_elapsed = time.perf_counter() - publish_start
        try:
            await asyncio.wait_for(done.wait(), timeout=float(timeout_s))
        except asyncio.TimeoutError:
            pass
        elapsed = time.perf_counter() - publish_start
        return _stats(
            name=f"{backend}.pubsub.{payload_bytes}B",
            backend=backend,
            category="pubsub",
            payload_bytes=payload_bytes,
            iterations=iterations,
            delivered=received,
            elapsed_s=elapsed,
            publish_elapsed_s=publish_elapsed,
            latencies_ns=latencies,
            ok=(received == int(iterations)),
        )
    except Exception as exc:
        elapsed = max(0.0, time.perf_counter() - publish_start)
        return _stats(
            name=f"{backend}.pubsub.{payload_bytes}B",
            backend=backend,
            category="pubsub",
            payload_bytes=payload_bytes,
            iterations=iterations,
            delivered=received,
            elapsed_s=elapsed,
            publish_elapsed_s=elapsed,
            latencies_ns=latencies,
            ok=False,
            note=f"{type(exc).__name__}: {exc}",
        )
    finally:
        await _stop_subscription(sub)
        await _close_transport(subscriber)
        await _close_transport(publisher)


async def bench_request_reply(
    *,
    backend: Literal["nats_core", "zenoh"],
    nats_url: str,
    run_id: str,
    payload_bytes: int,
    iterations: int,
    warmup_iterations: int,
    zenoh_shm_pool_bytes: int,
) -> BenchStats:
    subject = f"svc.{run_id}.endpoint.echo_{payload_bytes}"
    payload_tail = _payload(max(0, int(payload_bytes) - _MSG_HEADER.size), seed=payload_bytes + 7)
    if backend == "nats_core":
        client = _nats_transport(service_id=f"{run_id}_req_{payload_bytes}", nats_url=nats_url)
        server = _nats_transport(service_id=f"{run_id}_srv_{payload_bytes}", nats_url=nats_url)
    else:
        client = _zenoh_transport(service_id=f"{run_id}_req_{payload_bytes}", shm_pool_bytes=zenoh_shm_pool_bytes)
        server = _zenoh_transport(service_id=f"{run_id}_srv_{payload_bytes}", shm_pool_bytes=zenoh_shm_pool_bytes)
    await client.connect()
    await server.connect()

    async def _handler(raw: bytes) -> bytes:
        return raw

    handle = await server.serve(subject, _handler)
    await asyncio.sleep(0.05)
    for warmup_seq in range(max(0, int(warmup_iterations))):
        raw = _MSG_HEADER.pack(-1, time.perf_counter_ns()) + payload_tail
        await client.request(subject, raw, timeout=2.0, raise_on_error=True)
        if warmup_seq % 64 == 0:
            await asyncio.sleep(0)
    latencies: list[int] = []
    delivered = 0
    start = time.perf_counter()
    try:
        for seq in range(int(iterations)):
            raw = _MSG_HEADER.pack(seq, time.perf_counter_ns()) + payload_tail
            request_start = time.perf_counter_ns()
            response = await client.request(subject, raw, timeout=2.0, raise_on_error=True)
            if response is not None:
                delivered += 1
                latencies.append(time.perf_counter_ns() - request_start)
        elapsed = time.perf_counter() - start
        return _stats(
            name=f"{backend}.request_reply.{payload_bytes}B",
            backend=backend,
            category="request_reply",
            payload_bytes=payload_bytes,
            iterations=iterations,
            delivered=delivered,
            elapsed_s=elapsed,
            publish_elapsed_s=elapsed,
            latencies_ns=latencies,
            ok=(delivered == int(iterations)),
        )
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return _stats(
            name=f"{backend}.request_reply.{payload_bytes}B",
            backend=backend,
            category="request_reply",
            payload_bytes=payload_bytes,
            iterations=iterations,
            delivered=delivered,
            elapsed_s=elapsed,
            publish_elapsed_s=elapsed,
            latencies_ns=latencies,
            ok=False,
            note=f"{type(exc).__name__}: {exc}",
        )
    finally:
        await _stop_subscription(handle)
        await _close_transport(server)
        await _close_transport(client)


async def bench_kv(
    *,
    backend: Literal["nats_js_kv_memory", "nats_js_kv_file", "zenoh"],
    nats_url: str,
    run_id: str,
    payload_bytes: int,
    iterations: int,
    warmup_iterations: int,
    zenoh_shm_pool_bytes: int,
) -> list[BenchStats]:
    service_a = f"{run_id}_kva"
    service_b = f"{run_id}_kvb"
    payload = _payload(payload_bytes, seed=31)
    if backend == "nats_js_kv_memory":
        a = _nats_transport(service_id=service_a, nats_url=nats_url, storage=StorageType.MEMORY)
        b = _nats_transport(service_id=service_b, nats_url=nats_url, storage=StorageType.MEMORY)
    elif backend == "nats_js_kv_file":
        a = _nats_transport(service_id=f"{service_a}_file", nats_url=nats_url, storage=StorageType.FILE)
        b = _nats_transport(service_id=f"{service_b}_file", nats_url=nats_url, storage=StorageType.FILE)
        service_a = f"{service_a}_file"
    else:
        a = _zenoh_transport(service_id=service_a, shm_pool_bytes=zenoh_shm_pool_bytes)
        b = _zenoh_transport(service_id=service_b, shm_pool_bytes=zenoh_shm_pool_bytes)
    await a.connect()
    await b.connect()
    key = kv_key_node_state(node_id="bench", field=f"value.{payload_bytes}")
    results: list[BenchStats] = []
    try:
        for warmup_seq in range(max(0, int(warmup_iterations))):
            await a.kv_put(key, payload)
            _ = await a.kv_get(key)
            _ = await b.kv_get_in_bucket(kv_bucket_for_service(service_a), key, timeout=1.0)
            if warmup_seq % 64 == 0:
                await asyncio.sleep(0)

        put_lat: list[int] = []
        start = time.perf_counter()
        for _ in range(int(iterations)):
            t0 = time.perf_counter_ns()
            await a.kv_put(key, payload)
            put_lat.append(time.perf_counter_ns() - t0)
        elapsed = time.perf_counter() - start
        results.append(
            _stats(
                name=f"{backend}.kv_put.{payload_bytes}B",
                backend=backend,
                category="kv_put",
                payload_bytes=payload_bytes,
                iterations=iterations,
                delivered=iterations,
                elapsed_s=elapsed,
                publish_elapsed_s=elapsed,
                latencies_ns=put_lat,
            )
        )

        get_lat: list[int] = []
        get_ok = 0
        start = time.perf_counter()
        for _ in range(int(iterations)):
            t0 = time.perf_counter_ns()
            value = await a.kv_get(key)
            if value == payload:
                get_ok += 1
                get_lat.append(time.perf_counter_ns() - t0)
        elapsed = time.perf_counter() - start
        results.append(
            _stats(
                name=f"{backend}.kv_get_local.{payload_bytes}B",
                backend=backend,
                category="kv_get_local",
                payload_bytes=payload_bytes,
                iterations=iterations,
                delivered=get_ok,
                elapsed_s=elapsed,
                publish_elapsed_s=elapsed,
                latencies_ns=get_lat,
                ok=(get_ok == int(iterations)),
            )
        )

        remote_lat: list[int] = []
        remote_ok = 0
        start = time.perf_counter()
        for _ in range(int(iterations)):
            t0 = time.perf_counter_ns()
            value = await b.kv_get_in_bucket(kv_bucket_for_service(service_a), key, timeout=1.0)
            if value == payload:
                remote_ok += 1
                remote_lat.append(time.perf_counter_ns() - t0)
        elapsed = time.perf_counter() - start
        results.append(
            _stats(
                name=f"{backend}.kv_get_remote.{payload_bytes}B",
                backend=backend,
                category="kv_get_remote",
                payload_bytes=payload_bytes,
                iterations=iterations,
                delivered=remote_ok,
                elapsed_s=elapsed,
                publish_elapsed_s=elapsed,
                latencies_ns=remote_lat,
                ok=(remote_ok == int(iterations)),
            )
        )

        watch_key = kv_key_node_state(node_id="bench", field=f"watch.{payload_bytes}.{uuid.uuid4().hex}")
        received = 0
        watch_lat: list[int] = []
        done = asyncio.Event()

        async def _on_watch(_key: str, raw: bytes) -> None:
            nonlocal received
            if len(raw) < _MSG_HEADER.size:
                return
            seq, sent_ns = _MSG_HEADER.unpack_from(raw, 0)
            if seq < 0:
                return
            received += 1
            watch_lat.append(time.perf_counter_ns() - int(sent_ns))
            if received >= int(iterations):
                done.set()

        watch = await b.kv_watch_in_bucket(
            kv_bucket_for_service(service_a),
            "nodes.bench.state.watch.>",
            cb=_on_watch,
        )
        await asyncio.sleep(0.05)
        watch_payload_tail = _payload(max(0, int(payload_bytes) - _MSG_HEADER.size), seed=43)
        start = time.perf_counter()
        for seq in range(int(iterations)):
            raw = _MSG_HEADER.pack(seq, time.perf_counter_ns()) + watch_payload_tail
            await a.kv_put(watch_key, raw)
        publish_elapsed = time.perf_counter() - start
        try:
            await asyncio.wait_for(done.wait(), timeout=8.0)
        except asyncio.TimeoutError:
            pass
        elapsed = time.perf_counter() - start
        results.append(
            _stats(
                name=f"{backend}.kv_watch.{payload_bytes}B",
                backend=backend,
                category="kv_watch",
                payload_bytes=payload_bytes,
                iterations=iterations,
                delivered=received,
                elapsed_s=elapsed,
                publish_elapsed_s=publish_elapsed,
                latencies_ns=watch_lat,
                ok=(received == int(iterations)),
            )
        )
        await _stop_subscription(watch)
        return results
    finally:
        await _close_transport(b)
        await _close_transport(a)


async def bench_service_discovery(
    *,
    backend: Literal["nats_micro", "zenoh"],
    nats_url: str,
    run_id: str,
    iterations: int,
    warmup_iterations: int,
) -> BenchStats:
    service_id = f"{run_id}_discovery"
    latencies: list[int] = []
    delivered = 0

    if backend == "nats_micro":
        nc = await nats.connect(servers=[str(nats_url)], connect_timeout=2.0, allow_reconnect=False)
        micro = await add_service(
            nc,
            ServiceConfig(
                name=svc_micro_name(service_id),
                version="0.0.1",
                description="F8 runtime benchmark service discovery target",
            ),
        )
        subject = f"$SRV.PING.{svc_micro_name(service_id)}"
        await asyncio.sleep(0.05)
        start = time.perf_counter()
        try:
            for warmup_seq in range(max(0, int(warmup_iterations))):
                await nc.request(subject, b"", timeout=1.0)
                if warmup_seq % 64 == 0:
                    await asyncio.sleep(0)
            start = time.perf_counter()
            for _ in range(int(iterations)):
                t0 = time.perf_counter_ns()
                await nc.request(subject, b"", timeout=1.0)
                latencies.append(time.perf_counter_ns() - t0)
                delivered += 1
            elapsed = time.perf_counter() - start
            return _stats(
                name="nats_micro.service_discovery.ping",
                backend=backend,
                category="service_discovery",
                payload_bytes=0,
                iterations=iterations,
                delivered=delivered,
                elapsed_s=elapsed,
                publish_elapsed_s=elapsed,
                latencies_ns=latencies,
                ok=(delivered == int(iterations)),
                note="$SRV.PING.<service>",
            )
        except Exception as exc:
            elapsed = time.perf_counter() - start
            return _stats(
                name="nats_micro.service_discovery.ping",
                backend=backend,
                category="service_discovery",
                payload_bytes=0,
                iterations=iterations,
                delivered=delivered,
                elapsed_s=elapsed,
                publish_elapsed_s=elapsed,
                latencies_ns=latencies,
                ok=False,
                note=f"{type(exc).__name__}: {exc}",
            )
        finally:
            await micro.stop()
            await nc.close()

    try:
        import zenoh  # type: ignore[import-not-found]
    except ImportError as exc:
        return _stats(
            name="zenoh_liveliness.service_discovery.get",
            backend=backend,
            category="service_discovery",
            payload_bytes=0,
            iterations=iterations,
            delivered=0,
            elapsed_s=0.0,
            publish_elapsed_s=0.0,
            latencies_ns=[],
            ok=False,
            note=f"ImportError: {exc}",
        )

    async def _wait_liveliness_reply(replies: Any, *, timeout_s: float) -> bool:
        deadline = time.monotonic() + max(0.001, float(timeout_s))
        while time.monotonic() < deadline:
            try:
                reply = replies.try_recv()
            except zenoh.ZError as exc:  # type: ignore[attr-defined]
                if "channel is empty and closed" in str(exc).strip().lower():
                    return False
                raise
            if reply is None:
                await asyncio.sleep(0.001)
                continue
            if reply.ok is not None:
                return True
        return False

    provider_session = await asyncio.to_thread(zenoh.open, zenoh.Config())
    querier_session = await asyncio.to_thread(zenoh.open, zenoh.Config())
    token = None
    key = zenoh_service_liveliness_key(service_id)
    start = time.perf_counter()
    try:
        token = provider_session.liveliness().declare_token(key)
        await asyncio.sleep(0.05)
        for warmup_seq in range(max(0, int(warmup_iterations))):
            replies = querier_session.liveliness().get(key, timeout=1.0)
            await _wait_liveliness_reply(replies, timeout_s=1.0)
            if warmup_seq % 64 == 0:
                await asyncio.sleep(0)
        start = time.perf_counter()
        for _ in range(int(iterations)):
            t0 = time.perf_counter_ns()
            replies = querier_session.liveliness().get(key, timeout=1.0)
            if await _wait_liveliness_reply(replies, timeout_s=1.0):
                latencies.append(time.perf_counter_ns() - t0)
                delivered += 1
        elapsed = time.perf_counter() - start
        return _stats(
            name="zenoh_liveliness.service_discovery.get",
            backend=backend,
            category="service_discovery",
            payload_bytes=0,
            iterations=iterations,
            delivered=delivered,
            elapsed_s=elapsed,
            publish_elapsed_s=elapsed,
            latencies_ns=latencies,
            ok=(delivered == int(iterations)),
            note="liveliness.get(f8/live/svc/<service>)",
        )
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return _stats(
            name="zenoh_liveliness.service_discovery.get",
            backend=backend,
            category="service_discovery",
            payload_bytes=0,
            iterations=iterations,
            delivered=delivered,
            elapsed_s=elapsed,
            publish_elapsed_s=elapsed,
            latencies_ns=latencies,
            ok=False,
            note=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if token is not None:
            token.undeclare()
        await asyncio.to_thread(provider_session.close)
        await asyncio.to_thread(querier_session.close)


async def bench_nats_direct_video_payload(
    *,
    nats_url: str,
    run_id: str,
    width: int,
    height: int,
    channels: int,
) -> BenchStats:
    payload_bytes = int(width) * int(height) * int(channels)
    subject = f"svc.{run_id}.nodes.src.data.direct_video_{width}x{height}x{channels}"
    transport = _nats_transport(service_id=f"{run_id}_nats_direct_video", nats_url=nats_url)
    await transport.connect()
    latencies: list[int] = []
    start = time.perf_counter()
    try:
        t0 = time.perf_counter_ns()
        await transport.publish(subject, bytes(payload_bytes))
        client = await transport.require_client()
        await client.flush(timeout=1.0)
        latencies.append(time.perf_counter_ns() - t0)
        elapsed = time.perf_counter() - start
        return _stats(
            name=f"nats_core.video_direct_publish.{width}x{height}x{channels}",
            backend="nats_core",
            category="video_direct_publish",
            payload_bytes=payload_bytes,
            iterations=1,
            delivered=1,
            elapsed_s=elapsed,
            publish_elapsed_s=elapsed,
            latencies_ns=latencies,
            ok=True,
            note="direct frame payload accepted by default nats-server",
        )
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return _stats(
            name=f"nats_core.video_direct_publish.{width}x{height}x{channels}",
            backend="nats_core",
            category="video_direct_publish",
            payload_bytes=payload_bytes,
            iterations=1,
            delivered=0,
            elapsed_s=elapsed,
            publish_elapsed_s=elapsed,
            latencies_ns=latencies,
            ok=False,
            note=f"{type(exc).__name__}: {exc}",
        )
    finally:
        await _close_transport(transport)


def _frame_payload(frame_bytes: int) -> bytearray:
    payload = bytearray(frame_bytes)
    for idx in range(8, min(frame_bytes, 4096)):
        payload[idx] = idx % 251
    return payload


def bench_video_roundtrip(
    *,
    backend: Literal["legacy_shm", "zenoh"],
    run_id: str,
    width: int,
    height: int,
    channels: int,
    iterations: int,
    warmup_iterations: int,
    zenoh_shm_pool_bytes: int,
) -> BenchStats:
    pitch = int(width) * int(channels)
    frame_bytes = int(pitch) * int(height)
    payload = _frame_payload(frame_bytes)
    key_or_name = f"f8/bench/video/{run_id}/{backend}/{channels}" if backend == "zenoh" else f"bench.{run_id}.{channels}"
    latencies: list[int] = []
    delivered = 0
    start = time.perf_counter()
    publish_elapsed = 0.0
    if backend == "legacy_shm":
        transport = LegacyShmLatestVideoFrameTransport.open_read_writer(
            key_or_name,
            size=max(256 * 1024 * 1024, frame_bytes * 4),
            slot_count=2,
            use_event=True,
        )
        try:
            for _ in range(max(0, int(warmup_iterations))):
                _FRAME_TS_HEADER.pack_into(payload, 0, time.perf_counter_ns())
                transport.publish_frame(
                    width=width,
                    height=height,
                    pitch=pitch,
                    payload=payload,
                    fmt=VIDEO_FORMAT_BGRA32 if channels == 4 else 99,
                    ts_ms=int(time.time() * 1000),
                )
                frame = transport.wait_latest(timeout_ms=1000)
                if frame is not None:
                    frame.release()

            publish_total = 0.0
            for _ in range(int(iterations)):
                sent_ns = time.perf_counter_ns()
                _FRAME_TS_HEADER.pack_into(payload, 0, sent_ns)
                p0 = time.perf_counter()
                transport.publish_frame(
                    width=width,
                    height=height,
                    pitch=pitch,
                    payload=payload,
                    fmt=VIDEO_FORMAT_BGRA32 if channels == 4 else 99,
                    ts_ms=int(time.time() * 1000),
                )
                publish_total += time.perf_counter() - p0
                frame = transport.wait_latest(timeout_ms=1000)
                if frame is None:
                    continue
                try:
                    received_sent_ns = _FRAME_TS_HEADER.unpack_from(frame.payload, 0)[0]
                    latencies.append(time.perf_counter_ns() - int(received_sent_ns))
                    delivered += 1
                finally:
                    frame.release()
            elapsed = time.perf_counter() - start
            publish_elapsed = publish_total
        finally:
            transport.close(unlink=True)
    else:
        subscriber = ZenohLatestVideoFrameTransport.open_subscriber(
            key_or_name,
            shm_pool_bytes=zenoh_shm_pool_bytes,
        )
        publisher = ZenohLatestVideoFrameTransport.open_publisher(
            key_or_name,
            shm_pool_bytes=zenoh_shm_pool_bytes,
        )
        try:
            for _ in range(max(0, int(warmup_iterations))):
                _FRAME_TS_HEADER.pack_into(payload, 0, time.perf_counter_ns())
                publisher.publish_frame(
                    width=width,
                    height=height,
                    pitch=pitch,
                    payload=payload,
                    fmt=VIDEO_FORMAT_BGRA32 if channels == 4 else 99,
                    ts_ms=int(time.time() * 1000),
                )
                frame = subscriber.wait_latest(timeout_ms=1000)
                if frame is not None:
                    frame.release()

            publish_total = 0.0
            for _ in range(int(iterations)):
                sent_ns = time.perf_counter_ns()
                _FRAME_TS_HEADER.pack_into(payload, 0, sent_ns)
                p0 = time.perf_counter()
                publisher.publish_frame(
                    width=width,
                    height=height,
                    pitch=pitch,
                    payload=payload,
                    fmt=VIDEO_FORMAT_BGRA32 if channels == 4 else 99,
                    ts_ms=int(time.time() * 1000),
                )
                publish_total += time.perf_counter() - p0
                frame = subscriber.wait_latest(timeout_ms=1000)
                if frame is None:
                    continue
                try:
                    received_sent_ns = _FRAME_TS_HEADER.unpack_from(frame.payload, 0)[0]
                    latencies.append(time.perf_counter_ns() - int(received_sent_ns))
                    delivered += 1
                finally:
                    frame.release()
            elapsed = time.perf_counter() - start
            publish_elapsed = publish_total
        finally:
            subscriber.close()
            publisher.close()
    return _stats(
        name=f"{backend}.video_roundtrip.{width}x{height}x{channels}",
        backend=backend,
        category="video_roundtrip",
        payload_bytes=frame_bytes,
        iterations=iterations,
        delivered=delivered,
        elapsed_s=elapsed,
        publish_elapsed_s=publish_elapsed,
        latencies_ns=latencies,
        ok=(delivered == int(iterations)),
        note=("BGR24 synthetic fmt=99" if channels == 3 else "BGRA32 repo fmt"),
    )


def bench_video_firehose(
    *,
    backend: Literal["legacy_shm", "zenoh"],
    run_id: str,
    width: int,
    height: int,
    channels: int,
    iterations: int,
    warmup_iterations: int,
    zenoh_shm_pool_bytes: int,
) -> BenchStats:
    pitch = int(width) * int(channels)
    frame_bytes = int(pitch) * int(height)
    payload = _frame_payload(frame_bytes)
    key_or_name = (
        f"f8/bench/video_firehose/{run_id}/{backend}/{channels}"
        if backend == "zenoh"
        else f"bench.firehose.{run_id}.{channels}"
    )
    delivered = 0
    latencies: list[int] = []
    if backend == "legacy_shm":
        transport = LegacyShmLatestVideoFrameTransport.open_read_writer(
            key_or_name,
            size=max(256 * 1024 * 1024, frame_bytes * 4),
            slot_count=2,
            use_event=False,
        )
        try:
            for _ in range(max(0, int(warmup_iterations))):
                _FRAME_TS_HEADER.pack_into(payload, 0, time.perf_counter_ns())
                transport.publish_frame(
                    width=width,
                    height=height,
                    pitch=pitch,
                    payload=payload,
                    fmt=VIDEO_FORMAT_BGRA32 if channels == 4 else 99,
                    ts_ms=int(time.time() * 1000),
                )
                frame = transport.poll_latest()
                if frame is not None:
                    frame.release()

            start = time.perf_counter()
            for _ in range(int(iterations)):
                _FRAME_TS_HEADER.pack_into(payload, 0, time.perf_counter_ns())
                transport.publish_frame(
                    width=width,
                    height=height,
                    pitch=pitch,
                    payload=payload,
                    fmt=VIDEO_FORMAT_BGRA32 if channels == 4 else 99,
                    ts_ms=int(time.time() * 1000),
                )
            publish_elapsed = time.perf_counter() - start
            frame = transport.poll_latest()
            if frame is not None:
                try:
                    delivered = 1
                    sent_ns = _FRAME_TS_HEADER.unpack_from(frame.payload, 0)[0]
                    latencies.append(time.perf_counter_ns() - int(sent_ns))
                finally:
                    frame.release()
            elapsed = time.perf_counter() - start
        finally:
            transport.close(unlink=True)
    else:
        subscriber = ZenohLatestVideoFrameTransport.open_subscriber(
            key_or_name,
            shm_pool_bytes=zenoh_shm_pool_bytes,
        )
        publisher = ZenohLatestVideoFrameTransport.open_publisher(
            key_or_name,
            shm_pool_bytes=zenoh_shm_pool_bytes,
        )
        try:
            for _ in range(max(0, int(warmup_iterations))):
                _FRAME_TS_HEADER.pack_into(payload, 0, time.perf_counter_ns())
                publisher.publish_frame(
                    width=width,
                    height=height,
                    pitch=pitch,
                    payload=payload,
                    fmt=VIDEO_FORMAT_BGRA32 if channels == 4 else 99,
                    ts_ms=int(time.time() * 1000),
                )
                frame = subscriber.wait_latest(timeout_ms=1000)
                if frame is not None:
                    frame.release()

            start = time.perf_counter()
            for _ in range(int(iterations)):
                _FRAME_TS_HEADER.pack_into(payload, 0, time.perf_counter_ns())
                publisher.publish_frame(
                    width=width,
                    height=height,
                    pitch=pitch,
                    payload=payload,
                    fmt=VIDEO_FORMAT_BGRA32 if channels == 4 else 99,
                    ts_ms=int(time.time() * 1000),
                )
            publish_elapsed = time.perf_counter() - start
            deadline = time.monotonic() + 2.0
            frame = None
            while time.monotonic() < deadline:
                frame = subscriber.poll_latest()
                if frame is not None:
                    break
                time.sleep(0.001)
            if frame is not None:
                try:
                    delivered = 1
                    sent_ns = _FRAME_TS_HEADER.unpack_from(frame.payload, 0)[0]
                    latencies.append(time.perf_counter_ns() - int(sent_ns))
                finally:
                    frame.release()
            elapsed = time.perf_counter() - start
        finally:
            subscriber.close()
            publisher.close()
    return _stats(
        name=f"{backend}.video_firehose_latest.{width}x{height}x{channels}",
        backend=backend,
        category="video_firehose_latest",
        payload_bytes=frame_bytes,
        iterations=iterations,
        delivered=delivered,
        elapsed_s=elapsed,
        publish_elapsed_s=publish_elapsed,
        latencies_ns=latencies,
        ok=(delivered == 1),
        note=f"published {iterations} frames; delivered count intentionally latest-slot only",
    )


async def run_async(args: argparse.Namespace) -> BenchRun:
    run_id = "b" + uuid.uuid4().hex[:10]
    nats_server = NatsServer()
    nats_server.start()
    results: list[BenchStats] = []
    try:
        for payload_size in args.message_payloads:
            for backend in ("nats_core", "zenoh"):
                results.append(
                    await bench_pubsub(
                        backend=backend,
                        nats_url=nats_server.url,
                        run_id=run_id,
                        payload_bytes=int(payload_size),
                        iterations=int(args.message_iterations),
                        warmup_iterations=int(args.warmup_iterations),
                        timeout_s=float(args.message_timeout_s),
                        zenoh_shm_pool_bytes=int(args.zenoh_shm_pool_bytes),
                    )
                )
                results.append(
                    await bench_request_reply(
                        backend=backend,
                        nats_url=nats_server.url,
                        run_id=run_id,
                        payload_bytes=int(payload_size),
                        iterations=int(args.request_iterations),
                        warmup_iterations=int(args.warmup_iterations),
                        zenoh_shm_pool_bytes=int(args.zenoh_shm_pool_bytes),
                    )
                )

        for payload_size in args.kv_payloads:
            for backend in ("nats_js_kv_memory", "nats_js_kv_file", "zenoh"):
                results.extend(
                    await bench_kv(
                        backend=backend,
                        nats_url=nats_server.url,
                        run_id=run_id,
                        payload_bytes=int(payload_size),
                        iterations=int(args.kv_iterations),
                        warmup_iterations=int(args.warmup_iterations),
                        zenoh_shm_pool_bytes=int(args.zenoh_shm_pool_bytes),
                    )
                )

        for backend in ("nats_micro", "zenoh"):
            results.append(
                await bench_service_discovery(
                    backend=backend,
                    nats_url=nats_server.url,
                    run_id=run_id,
                    iterations=int(args.discovery_iterations),
                    warmup_iterations=int(args.warmup_iterations),
                )
            )

        results.append(
            await bench_nats_direct_video_payload(
                nats_url=nats_server.url,
                run_id=run_id,
                width=int(args.video_width),
                height=int(args.video_height),
                channels=3,
            )
        )

        for channels in (3, 4):
            for backend in ("legacy_shm", "zenoh"):
                results.append(
                    bench_video_roundtrip(
                        backend=backend,
                        run_id=run_id,
                        width=int(args.video_width),
                        height=int(args.video_height),
                        channels=channels,
                        iterations=int(args.video_iterations),
                        warmup_iterations=int(args.video_warmup_iterations),
                        zenoh_shm_pool_bytes=int(args.zenoh_shm_pool_bytes),
                    )
                )
                results.append(
                    bench_video_firehose(
                        backend=backend,
                        run_id=run_id,
                        width=int(args.video_width),
                        height=int(args.video_height),
                        channels=channels,
                        iterations=int(args.video_firehose_iterations),
                        warmup_iterations=int(args.video_warmup_iterations),
                        zenoh_shm_pool_bytes=int(args.zenoh_shm_pool_bytes),
                    )
                )

        return BenchRun(
            run_id=run_id,
            timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            machine=_machine_info(),
            nats_url=nats_server.url,
            results=results,
        )
    finally:
        nats_server.close()


def _print_table(results: list[BenchStats]) -> None:
    print(
        "name,backend,category,payload_bytes,iterations,delivered,lost,"
        "throughput_ops_s,publish_throughput_ops_s,throughput_mib_s,publish_throughput_mib_s,"
        "p50_ms,p95_ms,p99_ms,ok,note"
    )
    for item in results:
        print(
            f"{item.name},{item.backend},{item.category},{item.payload_bytes},{item.iterations},"
            f"{item.delivered},{item.lost},{item.throughput_ops_s:.3f},{item.publish_throughput_ops_s:.3f},"
            f"{item.throughput_mib_s:.3f},{item.publish_throughput_mib_s:.3f},"
            f"{'' if item.latency_p50_ms is None else f'{item.latency_p50_ms:.6f}'},"
            f"{'' if item.latency_p95_ms is None else f'{item.latency_p95_ms:.6f}'},"
            f"{'' if item.latency_p99_ms is None else f'{item.latency_p99_ms:.6f}'},"
            f"{item.ok},{item.note}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark NATS, JetStream KV, Zenoh, and legacy SHM transports.")
    parser.add_argument("--message-payloads", type=int, nargs="+", default=[64, 4096, 65536])
    parser.add_argument("--message-iterations", type=int, default=5000)
    parser.add_argument("--message-timeout-s", type=float, default=8.0)
    parser.add_argument("--request-iterations", type=int, default=1000)
    parser.add_argument("--kv-payloads", type=int, nargs="+", default=[64, 4096])
    parser.add_argument("--kv-iterations", type=int, default=1000)
    parser.add_argument("--discovery-iterations", type=int, default=1000)
    parser.add_argument("--warmup-iterations", type=int, default=100)
    parser.add_argument("--video-width", type=int, default=1920)
    parser.add_argument("--video-height", type=int, default=1080)
    parser.add_argument("--video-iterations", type=int, default=120)
    parser.add_argument("--video-warmup-iterations", type=int, default=5)
    parser.add_argument("--video-firehose-iterations", type=int, default=180)
    parser.add_argument("--zenoh-shm-pool-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark_results" / "runtime_transport_bench.json")
    args = parser.parse_args()

    run = asyncio.run(run_async(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(asdict(run), indent=2), encoding="utf-8")
    _print_table(run.results)
    print(f"\nWrote JSON results to {args.output}")


if __name__ == "__main__":
    main()

# Runtime Transport Benchmark Report: NATS / JetStream KV / Legacy SHM / Zenoh

Date: 2026-05-05

Raw data:

- Python/control/video benchmark: `benchmark_results/runtime_transport_bench_2026-05-05.json`
- C++ latest-video benchmark: `benchmark_results/cpp_latest_video_bench_2026-05-05.csv`

Benchmark harness: `scripts/bench_runtime_transports.py`

## Executive Summary

本次 benchmark 的结论比较明确：

- **Zenoh 可以继续作为 runtime 控制面默认后端推进**：本机 pub/sub 延迟低，KV/state update publish 快，liveliness 服务发现非常快，符合逐步替代 NATS 控制面和服务发现的方向。
- **NATS core 在 Python request/reply 上明显更快**：NATS request/reply p95 约 0.30-0.58 ms；当前 Zenoh query/request path p95 约 2.57-2.62 ms，吞吐约 400 req/s。生命周期控制、低频命令、状态设置可以接受；高频 RPC 不应依赖当前 Python Zenoh query path。
- **JetStream KV 的远端 get 仍然更强**：NATS JS KV remote get 约 3.7k-5.1k ops/s，p95 约 0.26-0.35 ms；Zenoh state queryable remote get 约 400 ops/s，p95 约 2.58 ms。Zenoh 方案胜在本地 service-owned state dict 读极快，且不需要 storage plugin；但它不是 JetStream KV 的集中存储等价替代。
- **Legacy SHM 目前不能删除，也不建议把生产 video 默认切到当前 Zenoh video path**：Python 1080p BGR24 下 legacy SHM roundtrip p95 0.74 ms，Zenoh roundtrip p95 12.34 ms。C++ 当前实现提升明显，1080p BGR24 Zenoh roundtrip 达到约 121 fps，但 p95 仍为 9.50 ms，未达到 `p95 < 5 ms` 的 video 目标。
- **NATS 不能直接承载 1080p BGR frame 默认 payload**：默认 `nats-server` 直接 publish 6,220,800 bytes 的 1080p BGR24 payload 失败，错误为 `MaxPayloadError: nats: maximum payload exceeded`。这验证了当前 “NATS 控制面 + 自制 SHM 视频数据面” 设计的历史合理性。

推荐迁移策略：

1. **继续 Zenoh-first 控制面**：服务发现、普通 data pub/sub、state update、service endpoint 都可以默认走 Zenoh。
2. **保留 NATS fallback**：尤其是需要极低延迟 request/reply 或 JetStream KV 集中存储语义的路径。
3. **video 数据面继续默认 legacy SHM，Zenoh video 作为 preview/实验路径**：等 C++ zenoh-cpp / zenoh-c SHM loaned sample path benchmark 通过 1080p60/120、p95 < 5 ms、无 backlog 后，再考虑切默认。当前 C++ benchmark 测到的是 repo 现有编码/解码实现，不是 loaned zero-copy path。

## Environment

| Item | Value |
|---|---|
| Host OS | Linux 6.8.0-111-generic x86_64, glibc 2.39 |
| CPU | Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz |
| CPU count | 12 |
| Python | 3.14.3, conda-forge, GCC 14.3.0 |
| Zenoh Python | eclipse-zenoh 1.9.0 |
| NATS Python | nats-py 2.14.0 |
| nats-server | v2.11.8 |
| NumPy | 2.4.2 |
| Zenoh SHM pool | 512 MiB |

## Methodology

Benchmark command:

```bash
pixi run python scripts/bench_runtime_transports.py \
  --message-payloads 64 4096 65536 \
  --message-iterations 5000 \
  --request-iterations 1000 \
  --kv-payloads 64 4096 \
  --kv-iterations 1000 \
  --discovery-iterations 1000 \
  --warmup-iterations 100 \
  --video-width 1920 \
  --video-height 1080 \
  --video-iterations 120 \
  --video-warmup-iterations 5 \
  --video-firehose-iterations 180 \
  --zenoh-shm-pool-bytes 536870912 \
  --output benchmark_results/runtime_transport_bench_2026-05-05.json
```

C++ latest-video command:

```bash
pixi run cpp_bench_latest_video \
  --video-width 1920 \
  --video-height 1080 \
  --video-iterations 120 \
  --video-firehose-iterations 180 \
  --video-warmup-iterations 5 \
  --zenoh-shm-pool-bytes 536870912 \
  > benchmark_results/cpp_latest_video_bench_2026-05-05.csv
```

Notes:

- NATS uses a local ephemeral `nats-server -js` with JetStream enabled.
- NATS JS KV is tested with both memory storage and file storage.
- Zenoh uses local sessions with shared-memory enabled through the repo transport config.
- Pub/sub tests publish a burst and measure receive latency from per-message send timestamp. This intentionally exposes queueing behavior.
- KV local get for Zenoh is the planned migration model: service-owned local dict, not a network or storage read.
- Zenoh remote get uses the repo queryable model, not Zenoh storage-manager.
- Video tests use synthetic 1080p frames:
  - BGR24: 1920 x 1080 x 3 = 6,220,800 bytes
  - BGRA32: 1920 x 1080 x 4 = 8,294,400 bytes
- Video firehose tests intentionally publish many frames and read only the latest slot afterward. `delivered=1` is expected there; use publish throughput for that scenario.
- The C++ Zenoh video benchmark measures the current repo implementation: frame metadata is encoded into `RuntimeBytes`, `zenoh::Session::put` publishes that payload, and the subscriber decodes into `LatestVideoFrame.payload`. It is not yet a loaned-buffer / zero-copy Zenoh SHM benchmark.

## Core Pub/Sub

| Backend | Payload | Delivered ops/s | Publish ops/s | Delivered MiB/s | Publish MiB/s | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NATS core | 64 B | 106,530 | 340,890 | 6.50 | 20.81 | 33.39 | 39.40 | 39.94 |
| Zenoh | 64 B | 10,390 | 10,416 | 0.63 | 0.64 | 0.61 | 1.15 | 1.22 |
| NATS core | 4 KiB | 70,828 | 148,084 | 276.67 | 578.45 | 31.91 | 35.87 | 36.76 |
| Zenoh | 4 KiB | 10,134 | 10,157 | 39.59 | 39.68 | 0.61 | 1.15 | 1.20 |
| NATS core | 64 KiB | 11,224 | 12,311 | 701.48 | 769.44 | 67.84 | 89.75 | 91.02 |
| Zenoh | 64 KiB | 8,732 | 8,750 | 545.75 | 546.90 | 0.64 | 1.21 | 1.26 |

Interpretation:

- NATS core has much higher burst publisher throughput, especially for small messages.
- Under unpaced burst, NATS receive latency becomes queueing latency: p95 reaches 39 ms at 64 B and 90 ms at 64 KiB.
- Zenoh's current Python path publishes at a more paced rate, so throughput is lower, but p95 stays around 1.15-1.21 ms in this test.
- For realtime control/event streams where stale backlog is undesirable, Zenoh behavior is attractive. For raw burst throughput, NATS core remains stronger.

## Request / Reply

| Backend | Payload | req/s | MiB/s | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| NATS core | 64 B | 4,374 | 0.27 | 0.23 | 0.30 | 0.33 |
| Zenoh query | 64 B | 398 | 0.02 | 2.48 | 2.60 | 3.59 |
| NATS core | 4 KiB | 4,310 | 16.84 | 0.22 | 0.31 | 0.35 |
| Zenoh query | 4 KiB | 402 | 1.57 | 2.48 | 2.57 | 2.63 |
| NATS core | 64 KiB | 2,298 | 143.62 | 0.41 | 0.58 | 0.73 |
| Zenoh query | 64 KiB | 395 | 24.67 | 2.51 | 2.62 | 2.71 |

Interpretation:

- NATS core request/reply is clearly better for high-frequency RPC.
- Current Python Zenoh query path is stable but roughly one order of magnitude lower throughput and about 2 ms slower at p95.
- Runtime lifecycle endpoints such as `activate`, `deactivate`, `set_state`, `set_rungraph`, `terminate` are not high-frequency enough for this to block migration.
- Avoid building hot data paths around Zenoh request/reply until C++ parity and query path optimization are measured.

## KV / State Model

### 64 B Payload

| Operation | Backend | ops/s | p50 ms | p95 ms | p99 ms |
|---|---|---:|---:|---:|---:|
| put | NATS JS KV memory | 5,939 | 0.170 | 0.218 | 0.236 |
| put | NATS JS KV file | 7,103 | 0.134 | 0.180 | 0.202 |
| put | Zenoh state update | 12,196 | 0.079 | 0.131 | 0.174 |
| local get | NATS JS KV memory | 4,593 | 0.217 | 0.280 | 0.319 |
| local get | NATS JS KV file | 4,880 | 0.198 | 0.267 | 0.300 |
| local get | Zenoh local dict | 2,859,749 | 0.000238 | 0.000260 | 0.000274 |
| remote get | NATS JS KV memory | 4,896 | 0.199 | 0.275 | 0.316 |
| remote get | NATS JS KV file | 5,069 | 0.188 | 0.257 | 0.344 |
| remote get | Zenoh queryable | 400 | 2.490 | 2.579 | 2.627 |
| watch | NATS JS KV memory | 4,374 | 0.234 | 0.333 | 0.385 |
| watch | NATS JS KV file | 3,980 | 0.263 | 0.386 | 0.425 |
| watch | Zenoh state update | 8,756 | 0.618 | 1.184 | 1.237 |

### 4 KiB Payload

| Operation | Backend | ops/s | p50 ms | p95 ms | p99 ms |
|---|---|---:|---:|---:|---:|
| put | NATS JS KV memory | 6,179 | 0.157 | 0.213 | 0.270 |
| put | NATS JS KV file | 5,763 | 0.159 | 0.224 | 0.411 |
| put | Zenoh state update | 11,512 | 0.083 | 0.141 | 0.172 |
| local get | NATS JS KV memory | 3,743 | 0.257 | 0.348 | 0.501 |
| local get | NATS JS KV file | 3,730 | 0.263 | 0.326 | 0.454 |
| local get | Zenoh local dict | 1,117,215 | 0.000658 | 0.000686 | 0.000699 |
| remote get | NATS JS KV memory | 3,824 | 0.252 | 0.342 | 0.486 |
| remote get | NATS JS KV file | 3,715 | 0.255 | 0.352 | 0.516 |
| remote get | Zenoh queryable | 400 | 2.494 | 2.581 | 2.663 |
| watch | NATS JS KV memory | 4,235 | 0.267 | 0.376 | 0.507 |
| watch | NATS JS KV file | 3,735 | 0.322 | 0.427 | 0.865 |
| watch | Zenoh state update | 10,482 | 0.621 | 1.134 | 1.217 |

Interpretation:

- Zenoh state update publish is faster than JS KV put in this local test.
- Zenoh local get is essentially a Python dict read, which is exactly the planned service-owned state model. This is not comparable to JetStream remote storage reads, but it is the desired fast path for service-local state.
- JetStream KV is much faster for remote get. If the system needs "centralized KV read from arbitrary peer" as a hot path, NATS JS KV is still better today.
- Zenoh watch throughput is good and higher than NATS JS KV watch here, with p95 around 1.1-1.2 ms.
- The proposed Zenoh state model should be treated as "latest service state exposure", not as a durable distributed KV store.

## Service Discovery

| Backend | Mechanism | ops/s | p50 ms | p95 ms | p99 ms |
|---|---|---:|---:|---:|---:|
| NATS micro | `$SRV.PING.<service>` | 4,110 | 0.247 | 0.304 | 0.344 |
| Zenoh | `liveliness.get(f8/live/svc/<service>)` | 132,547 | 0.006 | 0.012 | 0.014 |

Interpretation:

- Zenoh liveliness is extremely fast in this same-host local-session benchmark.
- This supports replacing NATS micro ping based discovery with Zenoh liveliness for PyStudio singleton guard and service readiness/presence.
- Cross-machine discovery should still be benchmarked separately with the intended router/peer topology, because multicast/router configuration can dominate discovery behavior outside localhost.

## Video / Large Payload Data Plane

### Direct NATS Payload Boundary

| Test | Payload | Result |
|---|---:|---|
| NATS core direct publish 1080p BGR24 | 6,220,800 B | failed: `MaxPayloadError: nats: maximum payload exceeded` |

Interpretation:

- Default NATS is not a direct video-frame transport for 1080p BGR.
- Raising max payload is possible, but it would still turn NATS into a large-copy message path and does not solve latest-frame/drop-stale semantics by itself.

### Latest-Frame Roundtrip

| Backend | Format | Payload | fps equivalent | MiB/s | p50 ms | p95 ms | p99 ms |
|---|---|---:|---:|---:|---:|---:|---:|
| Legacy SHM | BGR24 | 6,220,800 B | 1,439 | 8,538 | 0.606 | 0.736 | 0.799 |
| Zenoh video | BGR24 | 6,220,800 B | 63 | 375 | 10.797 | 12.338 | 13.052 |
| Legacy SHM | BGRA32 | 8,294,400 B | 1,763 | 13,943 | 0.432 | 0.819 | 0.925 |
| Zenoh video | BGRA32 | 8,294,400 B | 51 | 402 | 14.360 | 16.518 | 17.312 |

### Latest-Frame Firehose Publish

| Backend | Format | Published frames | Publish fps | Publish MiB/s | Latest latency ms |
|---|---|---:|---:|---:|---:|
| Legacy SHM | BGR24 | 180 | 1,778 | 10,551 | 0.511 |
| Zenoh video | BGR24 | 180 | 91 | 543 | 21.291 |
| Legacy SHM | BGRA32 | 180 | 1,966 | 15,550 | 0.477 |
| Zenoh video | BGRA32 | 180 | 66 | 522 | 28.417 |

Interpretation:

- Legacy SHM easily satisfies 1080p BGR 60/120 fps and p95 < 5 ms.
- Current Python Zenoh video path does not satisfy the original local video target:
  - BGR24 roundtrip is about 63 fps equivalent, but p95 is 12.34 ms.
  - BGR24 firehose publish is about 91 fps, below 120 fps, with latest latency around 21 ms.
  - BGRA32 is lower: roundtrip about 51 fps and firehose publish about 66 fps.
- The current Zenoh video implementation is acceptable for preview/monitor/low-rate streams, but not proven enough for primary local high-rate camera/video transport.
- The result likely includes Python payload construction/copy and does not prove the limit of Zenoh's C/C++ SHM loaned-buffer path.

### C++ Latest-Frame Roundtrip

| Backend | Format | Payload | fps equivalent | MiB/s | p50 ms | p95 ms | p99 ms |
|---|---|---:|---:|---:|---:|---:|---:|
| Legacy SHM C++ | BGR24 | 6,220,800 B | 668 | 3,964 | 1.445 | 1.919 | 2.474 |
| Zenoh C++ current | BGR24 | 6,220,800 B | 121 | 717 | 8.245 | 9.498 | 9.880 |
| Legacy SHM C++ | BGRA32 | 8,294,400 B | 635 | 5,025 | 1.510 | 2.035 | 2.144 |
| Zenoh C++ current | BGRA32 | 8,294,400 B | 90 | 714 | 10.733 | 13.087 | 15.126 |

### C++ Latest-Frame Firehose Publish

| Backend | Format | Published frames | Publish fps | Publish MiB/s | Latest latency ms |
|---|---|---:|---:|---:|---:|
| Legacy SHM C++ | BGR24 | 180 | 1,123 | 6,661 | 1.186 |
| Zenoh C++ current | BGR24 | 180 | 136 | 805 | 18.314 |
| Legacy SHM C++ | BGRA32 | 180 | 997 | 7,887 | 1.472 |
| Zenoh C++ current | BGRA32 | 180 | 106 | 836 | 22.659 |

C++ interpretation:

- C++ Zenoh is much better than the Python Zenoh video path for 1080p BGR24: roundtrip improves from about 63 fps / p95 12.34 ms to about 121 fps / p95 9.50 ms.
- It still does not match legacy SHM latency. Legacy SHM C++ p95 is about 1.9-2.0 ms; current Zenoh C++ is about 9.5 ms for BGR24 and 13.1 ms for BGRA32.
- Current C++ Zenoh BGR24 can roughly satisfy 120 fps throughput in this roundtrip loop, but it does not satisfy the original p95 < 5 ms latency target.
- Current C++ Zenoh BGRA32 is below 120 fps and p95 is above one 120 fps frame interval.
- The next meaningful optimization is to remove the extra encode/copy/decode path and benchmark a Zenoh SHM loaned-buffer API or a borrowed sample API with frame-view lifetime semantics.

## Migration Decision Matrix

| Area | Replace NATS / legacy now? | Reason |
|---|---|---|
| Service discovery | Yes, for Zenoh backend | Zenoh liveliness is fast and maps cleanly to service presence. |
| Control pub/sub | Yes, with NATS fallback | Zenoh p95 is low and avoids large stale queues in this benchmark. |
| Lifecycle endpoints | Yes, for normal control frequency | Zenoh request p95 around 2.6 ms is fine for lifecycle commands. |
| High-frequency request/reply | Not yet | NATS is still much faster and lower latency. |
| State update watch | Yes | Zenoh watch throughput is good; service-owned state model fits runtime state. |
| Remote KV get hot path | Not as a direct JS KV replacement | Zenoh queryable remote get is much slower than JS KV. Avoid hot polling. |
| Durable / centralized KV storage | No | Planned Zenoh model is service-owned latest state, not storage-manager-backed durability. |
| 1080p/4K local video | No | Legacy SHM remains much better on latency; current C++ Zenoh BGR24 reaches 120 fps throughput but misses p95 < 5 ms. |
| Cross-machine video | Experimental | Needs dedicated benchmark with actual topology and the optimized C++ loaned-buffer path. |

## Recommendation

Proceed with the Zenoh-first runtime migration for Python control plane, service discovery, state update distribution, and normal pub/sub communication.

Keep NATS as an explicit fallback during migration, especially for:

- high-frequency request/reply workloads;
- JetStream KV centralized storage semantics;
- regression testing against the previous backend.

Keep legacy SHM as the production default for local high-rate video until all of these are true:

1. C++ Zenoh SHM path uses loaned/zero-copy buffers rather than Python byte copies.
2. 1080p BGR24 60 fps and 120 fps both show no backlog.
3. p95 latency is below 5 ms, ideally with p99 below one frame interval.
4. 4K BGR/BGRA pool sizing has a clear failure mode when the pool is too small.
5. CPU usage is measured along with latency and throughput.

The practical near-term architecture should therefore be:

- **Zenoh default for control/state/discovery/pub-sub edges.**
- **NATS fallback for compatibility and specific KV/RPC cases.**
- **Legacy SHM default for local high-rate video data plane.**
- **Zenoh video behind an opt-in flag until the C++ loaned-buffer benchmark proves latency parity.**

## Follow-Up Benchmarks

Recommended next measurements:

1. Cross-machine Zenoh vs NATS pub/sub and request/reply over the intended router/peer topology.
2. C++ `zenoh-cpp` / `zenoh-c` latest-frame transport with SHM loaned buffers.
3. 1080p/4K video with real camera/screencap producers and PyStudio consumers.
4. CPU utilization and memory bandwidth while running video plus control traffic concurrently.
5. Slow-consumer latest-frame test that asserts skipped old frames and bounded queue depth under actual service code.

from __future__ import annotations

"""
Internal service bus package.

This package remains because concrete owner modules still live under:
- `f8pysdk.service_bus.config`
- `f8pysdk.service_bus.runtime`
- `f8pysdk.service_bus.data.*`
- `f8pysdk.service_bus.state.*`
- `f8pysdk.service_bus.internal.*`

Do not treat `f8pysdk.service_bus` itself as a public barrel.

Stable public SDK entrypoints live under:
- `f8pysdk.bus`
- `f8pysdk.app`
- `f8pysdk.specs`
- `f8pysdk.command`
- `f8pysdk.data`
- `f8pysdk.nodes`
- `f8pysdk.registry`
- `f8pysdk.state`
- `f8pysdk.transport`
- `f8pysdk.testing`
"""

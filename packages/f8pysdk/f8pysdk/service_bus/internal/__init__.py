from __future__ import annotations

"""
Explicit internal-only service bus boundary.

Import concrete owner modules directly:
- `f8pysdk.service_bus.internal.data`
- `f8pysdk.service_bus.internal.state`
- `f8pysdk.service_bus.internal.micro`
- `f8pysdk.service_bus.internal.command`
- `f8pysdk.service_bus.internal.cache`
- `f8pysdk.service_bus.internal.logging`

State runtime owners live under:
- `f8pysdk.service_bus.state.read`
- `f8pysdk.service_bus.state.write`
- `f8pysdk.service_bus.state.pipeline`
- `f8pysdk.service_bus.state.store`
- `f8pysdk.service_bus.state.router`
- `f8pysdk.service_bus.state.helpers`

Data runtime owners live under:
- `f8pysdk.service_bus.data.emit`
- `f8pysdk.service_bus.data.flow`
- `f8pysdk.service_bus.data.router`
"""

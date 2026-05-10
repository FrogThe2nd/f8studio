from __future__ import annotations

from typing import Any

from f8pysdk.rungraph_fingerprint import build_rungraph_deploy_fingerprint, build_rungraph_deploy_snapshot

from ..nodegraph.runtime_compiler import CompiledRuntimeGraphs


def build_compiled_deploy_snapshot(compiled: CompiledRuntimeGraphs) -> dict[str, Any]:
    return build_rungraph_deploy_snapshot(compiled.global_graph)


def build_compiled_deploy_fingerprint(compiled: CompiledRuntimeGraphs) -> str:
    return build_rungraph_deploy_fingerprint(compiled.global_graph)


__all__ = [
    "build_compiled_deploy_fingerprint",
    "build_compiled_deploy_snapshot",
]

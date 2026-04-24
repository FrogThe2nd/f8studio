from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _scenario_describe() -> dict[str, Any]:
    from f8pystudio.app.program import PyStudioProgram

    program = PyStudioProgram()
    payload = program.describe_json()
    return {"payload_keys": len(payload)}


def _reset_global_registries() -> None:
    from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
    from f8pystudio.render_nodes import RenderNodeRegistry

    ServiceCatalog.instance().clear()
    RenderNodeRegistry._instance = None


def _inject_pystudio_specs(catalog: Any) -> str | None:
    from f8pystudio.studio_specs.registry import SERVICE_CLASS, create_pystudio_registry

    registry = create_pystudio_registry()
    service_spec = registry.service_spec(SERVICE_CLASS)
    if service_spec is None:
        return None
    catalog.register_service(service_spec)
    for operator_spec in registry.operator_specs(SERVICE_CLASS):
        catalog.register_operator(operator_spec)
    return str(service_spec.serviceClass)


def _scenario_discovery() -> dict[str, Any]:
    from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
    from f8pysdk.service_runtime_tools.inventory.discovery import load_discovery_into_catalog

    catalog = ServiceCatalog.instance()
    catalog.clear()
    found = load_discovery_into_catalog(catalog=catalog, builtin_injectors=(_inject_pystudio_specs,))
    service_count = len(catalog.services.all())
    operator_count = len(catalog.operators.all())
    return {"found_count": len(found), "service_count": service_count, "operator_count": operator_count}


def _scenario_build_node_classes() -> dict[str, Any]:
    from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
    from f8pysdk.service_runtime_tools.inventory.discovery import load_discovery_into_catalog
    from f8pystudio.app.program import PyStudioProgram
    from f8pystudio.studio_specs.registry import shared_pystudio_registry

    _reset_global_registries()
    program = PyStudioProgram()
    registry = shared_pystudio_registry()
    manifests = program._load_plugin_manifests()
    program._apply_plugin_manifests_to_runtime_registry(manifests, registry=registry)
    load_discovery_into_catalog(
        catalog=ServiceCatalog.instance(),
        builtin_injectors=(lambda catalog: program._inject_pystudio_specs_from_registry(catalog, registry=registry),),
    )
    program._apply_plugin_manifests_to_renderers(manifests)
    node_classes = program.build_node_classes()
    return {"node_class_count": len(node_classes)}


SCENARIOS: dict[str, Callable[[], dict[str, Any]]] = {
    "describe": _scenario_describe,
    "discovery": _scenario_discovery,
    "build_node_classes": _scenario_build_node_classes,
}


def _scenario_deploy_fingerprint() -> dict[str, Any]:
    from f8pysdk.specs import (
        F8DataPortSpec,
        F8Edge,
        F8EdgeKindEnum,
        F8RuntimeGraph,
        F8RuntimeNode,
        F8RuntimeService,
        F8StateAccess,
        F8StateSpec,
        F8StringTypeSchema,
    )
    from f8pystudio.bridge.deploy_fingerprint import build_compiled_deploy_fingerprint
    from f8pystudio.nodegraph.runtime_compiler import CompiledRuntimeGraphs

    schema = F8StringTypeSchema()
    data_ports = [F8DataPortSpec(name=f"data_{index}", valueSchema=schema) for index in range(4)]
    state_fields = [
        F8StateSpec(name=f"state_{index}", valueSchema=schema, access=F8StateAccess.rw)
        for index in range(4)
    ]
    services = [
        F8RuntimeService(serviceId=f"svc_{index}", serviceClass="f8.test", label=f"Service {index}")
        for index in range(16)
    ]
    nodes = [
        F8RuntimeNode(
            nodeId=f"node_{index:04d}",
            serviceId=f"svc_{index % len(services)}",
            serviceClass="f8.test",
            operatorClass="f8.test.op",
            execInPorts=["in"],
            execOutPorts=["out"],
            dataInPorts=data_ports,
            dataOutPorts=data_ports,
            stateFields=state_fields,
            stateValues={"ignored": index},
        )
        for index in range(512)
    ]
    edges = [
        F8Edge(
            edgeId=f"edge_{index:04d}",
            fromServiceId=f"svc_{index % len(services)}",
            fromOperatorId=f"node_{index:04d}",
            fromPort="out",
            toServiceId=f"svc_{(index + 1) % len(services)}",
            toOperatorId=f"node_{(index + 1) % len(nodes):04d}",
            toPort="in",
            kind=F8EdgeKindEnum.data,
        )
        for index in range(1024)
    ]
    graph = F8RuntimeGraph(graphId="graph", revision="1", services=services, nodes=nodes, edges=edges)
    fingerprint = build_compiled_deploy_fingerprint(
        CompiledRuntimeGraphs(global_graph=graph, per_service={}, warnings=())
    )
    return {"fingerprint_bytes": len(fingerprint), "nodes": len(nodes), "edges": len(edges)}


SCENARIOS["deploy_fingerprint"] = _scenario_deploy_fingerprint


def _profile_once(scenario: str, *, top: int) -> tuple[float, dict[str, Any], list[dict[str, Any]]]:
    target = SCENARIOS[scenario]
    profile = cProfile.Profile()
    started = time.perf_counter()
    details = profile.runcall(target)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    stats_path = Path(tempfile.gettempdir()) / f"f8pystudio-{scenario}.prof"
    profile.dump_stats(str(stats_path))
    stats = pstats.Stats(profile).sort_stats("cumtime")
    stats.calc_callees()
    hot: list[dict[str, Any]] = []
    for func in stats.fcn_list[:top]:
        primitive_calls, total_calls, total_time, cumulative_time, _callers = stats.stats[func]
        filename, line_number, func_name = func
        hot.append(
            {
                "file": filename,
                "line": line_number,
                "func": func_name,
                "calls": total_calls,
                "primitive_calls": primitive_calls,
                "total_ms": total_time * 1000.0,
                "cum_ms": cumulative_time * 1000.0,
            }
        )
    details["profile_path"] = str(stats_path)
    return elapsed_ms, details, hot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="describe")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    elapsed_values: list[float] = []
    details: dict[str, Any] = {}
    hot: list[dict[str, Any]] = []
    for _ in range(max(1, args.repeat)):
        elapsed_ms, details, hot = _profile_once(args.scenario, top=max(1, args.top))
        elapsed_values.append(elapsed_ms)

    result = {
        "scenario": args.scenario,
        "repeat": len(elapsed_values),
        "elapsed_ms_median": statistics.median(elapsed_values),
        "elapsed_ms_min": min(elapsed_values),
        "elapsed_ms_max": max(elapsed_values),
        "details": details,
        "hot": hot,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

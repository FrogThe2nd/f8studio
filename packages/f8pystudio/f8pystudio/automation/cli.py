from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .client import AutomationClient
from .gui_host import launch_pystudio_with_automation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F8PyStudio automation CLI")
    parser.add_argument("--connection-file", default="", help="Automation connection JSON path.")
    sub = parser.add_subparsers(dest="command", required=True)

    launch = sub.add_parser("launch", help="Launch PyStudio with automation enabled.")
    launch.add_argument("--automation", action="store_true", help="Accepted for readability; automation is always enabled.")
    launch.add_argument("--port-file", default="", help="Connection metadata path.")
    launch.add_argument("--token-file", default="", help="Token file path.")
    launch.add_argument("--timeout", type=float, default=20.0)

    sub.add_parser("status", help="Show attached Studio status.")

    graph = sub.add_parser("graph", help="Graph operations.")
    graph_sub = graph.add_subparsers(dest="graph_command", required=True)
    graph_sub.add_parser("snapshot", help="Print current graph snapshot.")
    find_nodes = graph_sub.add_parser("find", help="Find graph nodes.")
    find_nodes.add_argument("--query", default="")
    find_nodes.add_argument("--node-id", default="")
    find_nodes.add_argument("--node-type", default="")
    find_nodes.add_argument("--kind", default="")
    find_nodes.add_argument("--service-class", default="")
    find_nodes.add_argument("--operator-class", default="")
    find_nodes.add_argument("--selected-only", action="store_true")
    find_nodes.add_argument("--limit", type=int, default=50)
    detail = graph_sub.add_parser("detail", help="Print one graph node detail.")
    detail.add_argument("--node-id", required=True)
    connections = graph_sub.add_parser("connections", help="Print graph connections.")
    connections.add_argument("--node-id", default="")
    connections.add_argument("--direction", choices=("both", "incoming", "outgoing"), default="both")
    connections.add_argument("--limit", type=int, default=200)
    graph_sub.add_parser("diagnostics", help="Print graph diagnostics.")
    graph_sub.add_parser("catalog", help="Print node catalog.")
    graph_sub.add_parser("compile", help="Compile current graph.")
    patch = graph_sub.add_parser("patch", help="Preview or apply a graph patch JSON file.")
    patch.add_argument("--file", required=True)
    patch.add_argument("--dry-run", action="store_true")
    patch.add_argument("--confirm", action="store_true")

    runtime = sub.add_parser("runtime", help="Runtime operations.")
    runtime_sub = runtime.add_subparsers(dest="runtime_command", required=True)
    deploy = runtime_sub.add_parser("deploy", help="Deploy current rungraph.")
    deploy.add_argument("--wait", action="store_true")
    deploy.add_argument("--confirm", action="store_true")
    state = runtime_sub.add_parser("state", help="Read runtime state.")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    state_get = state_sub.add_parser("get")
    state_get.add_argument("--service-id", required=True)
    state_get.add_argument("--node-id", required=True)
    state_get.add_argument("--field", required=True)
    state_watch = state_sub.add_parser("watch")
    state_watch.add_argument("--service-id", required=True)
    state_watch.add_argument("--node-id", required=True)
    state_watch.add_argument("--field", required=True)
    state_watch.add_argument("--duration-ms", type=int, default=1000)
    state_watch.add_argument("--after-ts-ms", type=int, default=0)
    state_set = state_sub.add_parser("set")
    state_set.add_argument("--service-id", required=True)
    state_set.add_argument("--node-id", required=True)
    state_set.add_argument("--field", required=True)
    state_set.add_argument("--value-json", required=True)
    state_set.add_argument("--timeout", type=float, default=2.0)
    monitor = runtime_sub.add_parser("monitor", help="Read runtime monitor.")
    monitor_sub = monitor.add_subparsers(dest="monitor_command", required=True)
    monitor_latest = monitor_sub.add_parser("latest")
    monitor_latest.add_argument("--service-id", default="")
    monitor_latest.add_argument("--limit", type=int, default=500)
    port = runtime_sub.add_parser("port", help="Read data port samples.")
    port_sub = port.add_subparsers(dest="port_command", required=True)
    sample = port_sub.add_parser("sample")
    sample.add_argument("--service-id", required=True)
    sample.add_argument("--node-id", required=True)
    sample.add_argument("--port", required=True)
    sample.add_argument("--limit", type=int, default=1)
    sample.add_argument("--timeout", type=float, default=2.0)
    sample.add_argument("--include-value", action=argparse.BooleanOptionalAction, default=True)
    sample.add_argument("--max-value-bytes", type=int, default=65536)
    sample.add_argument("--cached-only", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "launch":
        info = launch_pystudio_with_automation(
            port_file=str(args.port_file or "") or None,
            token_file=str(args.token_file or "") or None,
            timeout_s=float(args.timeout),
        )
        _print_json(info.to_dict())
        return 0

    client = _client_from_args(args)
    if args.command == "status":
        _print_json(client.call("studio.status"))
        return 0
    if args.command == "graph":
        return _handle_graph(client, args)
    if args.command == "runtime":
        return _handle_runtime(client, args)
    raise ValueError(f"unsupported command: {args.command}")


def _client_from_args(args: argparse.Namespace) -> AutomationClient:
    path = str(args.connection_file or "").strip() or None
    return AutomationClient.from_connection_file(path)


def _handle_graph(client: AutomationClient, args: argparse.Namespace) -> int:
    if args.graph_command == "snapshot":
        _print_json(client.call("graph.snapshot"))
        return 0
    if args.graph_command == "find":
        _print_json(
            client.call(
                "graph.findNodes",
                {
                    "query": args.query,
                    "nodeId": args.node_id,
                    "nodeType": args.node_type,
                    "kind": args.kind,
                    "serviceClass": args.service_class,
                    "operatorClass": args.operator_class,
                    "selectedOnly": bool(args.selected_only),
                    "limit": int(args.limit),
                },
            )
        )
        return 0
    if args.graph_command == "detail":
        _print_json(client.call("graph.nodeDetail", {"nodeId": args.node_id}))
        return 0
    if args.graph_command == "connections":
        _print_json(
            client.call(
                "graph.connections",
                {"nodeId": args.node_id, "direction": args.direction, "limit": int(args.limit)},
            )
        )
        return 0
    if args.graph_command == "diagnostics":
        _print_json(client.call("graph.diagnostics"))
        return 0
    if args.graph_command == "catalog":
        _print_json(client.call("graph.catalog"))
        return 0
    if args.graph_command == "compile":
        _print_json(client.call("graph.compile"))
        return 0
    if args.graph_command == "patch":
        patch_payload = _read_json_file(args.file)
        method = "graph.previewPatch" if bool(args.dry_run) else "graph.applyPatch"
        _print_json(client.call(method, {"patch": patch_payload, "confirm": bool(args.confirm)}))
        return 0
    raise ValueError(f"unsupported graph command: {args.graph_command}")


def _handle_runtime(client: AutomationClient, args: argparse.Namespace) -> int:
    if args.runtime_command == "deploy":
        _print_json(client.call("runtime.deploy", {"confirm": bool(args.confirm), "wait": bool(args.wait)}))
        return 0
    if args.runtime_command == "state" and args.state_command == "get":
        _print_json(
            client.call(
                "runtime.readState",
                {"serviceId": args.service_id, "nodeId": args.node_id, "field": args.field},
            )
        )
        return 0
    if args.runtime_command == "state" and args.state_command == "watch":
        payload: dict[str, Any] = {
            "serviceId": args.service_id,
            "nodeId": args.node_id,
            "field": args.field,
            "durationMs": int(args.duration_ms),
        }
        if int(args.after_ts_ms) > 0:
            payload["afterTsMs"] = int(args.after_ts_ms)
        _print_json(client.call("runtime.watchState", payload))
        return 0
    if args.runtime_command == "state" and args.state_command == "set":
        _print_json(
            client.call(
                "runtime.writeState",
                {
                    "serviceId": args.service_id,
                    "nodeId": args.node_id,
                    "field": args.field,
                    "value": json.loads(str(args.value_json)),
                    "timeoutS": float(args.timeout),
                },
            )
        )
        return 0
    if args.runtime_command == "monitor" and args.monitor_command == "latest":
        _print_json(client.call("runtime.readMonitor", {"serviceId": args.service_id, "limit": int(args.limit)}))
        return 0
    if args.runtime_command == "port" and args.port_command == "sample":
        _print_json(
            client.call(
                "runtime.samplePort",
                {
                    "serviceId": args.service_id,
                    "nodeId": args.node_id,
                    "port": args.port,
                    "limit": int(args.limit),
                    "timeoutS": float(args.timeout),
                    "includeValue": bool(args.include_value),
                    "maxValueBytes": int(args.max_value_bytes),
                    "subscribe": not bool(args.cached_only),
                },
            )
        )
        return 0
    raise ValueError(f"unsupported runtime command: {args.runtime_command}")


def _read_json_file(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())

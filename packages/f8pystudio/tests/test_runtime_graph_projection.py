from __future__ import annotations

from types import SimpleNamespace

from f8pystudio.bridge.runtime_graph_projection import (
    build_local_state_field_index,
    build_remote_watch_targets,
    build_studio_runtime_graph,
    dedupe_fields,
)


def _compiled(
    *,
    nodes: list[object] | None = None,
    graph_id: str = "graph.demo",
    revision: str = "rev.1",
    per_service: dict[str, object] | None = None,
) -> object:
    return SimpleNamespace(
        global_graph=SimpleNamespace(graphId=graph_id, revision=revision, nodes=list(nodes or [])),
        per_service=dict(per_service or {}),
    )


def test_dedupe_fields_preserves_order() -> None:
    assert dedupe_fields(["a", "b", "a", "c", "b"]) == ("a", "b", "c")


def test_build_remote_watch_targets_adds_required_fields_and_sorts() -> None:
    invalid_messages: list[str] = []
    compiled = _compiled(
        nodes=[
            SimpleNamespace(
                serviceId="svc_b",
                nodeId="node_2",
                operatorClass="",
                stateFields=[SimpleNamespace(name="x"), SimpleNamespace(name="svcId"), SimpleNamespace(name="x")],
            ),
            SimpleNamespace(
                serviceId="",
                nodeId="bad_node",
                operatorClass="f8.bad",
                stateFields=[],
            ),
            SimpleNamespace(
                serviceId="svc_a",
                nodeId="node_1",
                operatorClass="f8.op",
                stateFields=[SimpleNamespace(name="alpha"), SimpleNamespace(name="")],
            ),
        ]
    )

    targets = build_remote_watch_targets(
        compiled,
        on_invalid_target=lambda message: invalid_messages.append(str(message)),
    )

    assert [target.service_id for target in targets] == ["svc_a", "svc_b"]
    assert [target.node_id for target in targets] == ["node_1", "node_2"]
    assert targets[0].fields == ("alpha", "svcId", "operatorId")
    assert targets[1].fields == ("x", "svcId")
    assert invalid_messages


def test_build_local_state_field_index_for_studio_subgraph() -> None:
    studio_graph = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                nodeId="nodeA",
                stateFields=[
                    SimpleNamespace(name="gain"),
                    SimpleNamespace(name="gain"),
                    SimpleNamespace(name="enabled"),
                ],
            ),
            SimpleNamespace(nodeId="", stateFields=[SimpleNamespace(name="ignored")]),
        ]
    )
    compiled = _compiled(per_service={"studio.default": studio_graph})

    index = build_local_state_field_index(compiled, studio_service_id="studio.default")

    assert index == {"nodeA": ("gain", "enabled")}


def test_build_studio_runtime_graph_uses_global_metadata() -> None:
    compiled = _compiled(graph_id="main.graph", revision="42")

    graph = build_studio_runtime_graph(compiled, studio_service_id="studio.default")

    assert graph.graphId == "main.graph"
    assert graph.revision == "42"
    assert graph.meta.source == "studio"

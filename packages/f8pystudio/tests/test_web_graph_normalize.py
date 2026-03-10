from __future__ import annotations

from f8pystudio.web.graph_doc import (
    GraphDoc,
    GraphEdge,
    GraphEdgeEndpoint,
    GraphNode,
    GraphNodeUi,
    normalize_graph_doc,
)


def test_normalize_graph_doc_drops_missing_endpoints_and_uniquifies_edge_ids() -> None:
    doc = GraphDoc(
        graphId="g",
        revision="1",
        nodes=[
            GraphNode(id="a", nodeType="svc.f8.demo", spec={}, ui=GraphNodeUi()),
            GraphNode(id="b", nodeType="svc.f8.demo", spec={}, ui=GraphNodeUi()),
        ],
        edges=[
            GraphEdge(
                id="e",
                kind="data",
                from_=GraphEdgeEndpoint(nodeId="a", port="x"),
                to=GraphEdgeEndpoint(nodeId="b", port="y"),
            ),
            # Duplicate id is allowed in input but must be unique after normalize.
            GraphEdge(
                id="e",
                kind="data",
                from_=GraphEdgeEndpoint(nodeId="a", port="x2"),
                to=GraphEdgeEndpoint(nodeId="b", port="y2"),
            ),
            # Missing node endpoint should be dropped.
            GraphEdge(
                id="e3",
                kind="data",
                from_=GraphEdgeEndpoint(nodeId="a", port="x"),
                to=GraphEdgeEndpoint(nodeId="MISSING", port="y"),
            ),
        ],
    )

    out = normalize_graph_doc(doc)
    ids = [e.id for e in list(out.doc.edges)]
    assert len(ids) == 2
    assert len(set(ids)) == 2


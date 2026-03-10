from __future__ import annotations

import json
from pathlib import Path

from f8pystudio.web.connection_validate import ConnectionEndpoint, validate_connection
from f8pystudio.web.session_codec import import_nodegraphqt_session


def _scenario_path(name: str) -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "docs" / "scenarios" / "scripts" / name


def test_validate_connection_accepts_imported_edges_from_session() -> None:
    src_path = _scenario_path("scene-02-gamemod_skeleton.json")
    payload = json.loads(src_path.read_text(encoding="utf-8"))
    doc = import_nodegraphqt_session(payload).doc

    assert doc.edges, "scenario must contain edges"
    for e in list(doc.edges):
        allowed, reason = validate_connection(
            doc,
            kind=str(e.kind),
            from_ep=ConnectionEndpoint(nodeId=str(e.from_.nodeId), port=str(e.from_.port)),
            to_ep=ConnectionEndpoint(nodeId=str(e.to.nodeId), port=str(e.to.port)),
        )
        assert allowed, f"expected edge to be valid: {e.id} reason={reason!r}"


def test_validate_connection_rejects_unknown_ports() -> None:
    src_path = _scenario_path("scene-02-gamemod_skeleton.json")
    payload = json.loads(src_path.read_text(encoding="utf-8"))
    doc = import_nodegraphqt_session(payload).doc

    e = list(doc.edges)[0]
    allowed, reason = validate_connection(
        doc,
        kind=str(e.kind),
        from_ep=ConnectionEndpoint(nodeId=str(e.from_.nodeId), port="__no_such_port__"),
        to_ep=ConnectionEndpoint(nodeId=str(e.to.nodeId), port=str(e.to.port)),
    )
    assert not allowed
    assert reason


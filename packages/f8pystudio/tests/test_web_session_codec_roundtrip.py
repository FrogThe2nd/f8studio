from __future__ import annotations

import json
from pathlib import Path

from f8pystudio.web.session_codec import export_nodegraphqt_session, import_nodegraphqt_session


def _scenario_path(name: str) -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "docs" / "scenarios" / "scripts" / name


def test_nodegraphqt_session_roundtrip_preserves_connections() -> None:
    src_path = _scenario_path("scene-02-gamemod_skeleton.json")
    payload = json.loads(src_path.read_text(encoding="utf-8"))
    out = import_nodegraphqt_session(payload)
    exported = export_nodegraphqt_session(out.doc)

    assert exported.get("schemaVersion") == payload.get("schemaVersion") == "f8studio-session/1"
    layout_in = payload.get("layout")
    layout_out = exported.get("layout")
    assert isinstance(layout_in, dict)
    assert isinstance(layout_out, dict)

    nodes_in = layout_in.get("nodes")
    nodes_out = layout_out.get("nodes")
    assert isinstance(nodes_in, dict)
    assert isinstance(nodes_out, dict)
    assert set(nodes_out.keys()) == set(nodes_in.keys())

    conns_in = layout_in.get("connections")
    conns_out = layout_out.get("connections")
    assert isinstance(conns_in, list)
    assert isinstance(conns_out, list)
    assert conns_out == conns_in

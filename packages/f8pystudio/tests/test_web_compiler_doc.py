from __future__ import annotations

import json
from pathlib import Path

from f8pystudio.web.compiler_doc import compile_runtime_graphs_from_doc
from f8pystudio.web.session_codec import import_nodegraphqt_session


def _scenario_path(name: str) -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "docs" / "scenarios" / "scripts" / name


def test_compile_from_doc_produces_per_service_graphs() -> None:
    src_path = _scenario_path("scene-02-gamemod_skeleton.json")
    payload = json.loads(src_path.read_text(encoding="utf-8"))
    doc = import_nodegraphqt_session(payload).doc

    compiled = compile_runtime_graphs_from_doc(doc)

    assert str(compiled.global_graph.graphId) == "studio"
    assert str(compiled.global_graph.revision) == "1"
    # Per-service graphs should exist for each declared service in the global graph.
    service_ids = {str(s.serviceId) for s in list(compiled.global_graph.services or [])}
    assert service_ids
    assert service_ids.issubset(set(compiled.per_service.keys()))

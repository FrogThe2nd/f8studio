from __future__ import annotations

from pathlib import Path


ASSET_DIR = Path(__file__).resolve().parents[1] / "f8pystudio" / "render_nodes" / "web_assets" / "viz_three_d"


def test_skeleton_tree_uses_explicit_expand_row_instead_of_native_summary() -> None:
    viewer_js = (ASSET_DIR / "viewer.js").read_text(encoding="utf-8")
    index_html = (ASSET_DIR / "index.html").read_text(encoding="utf-8")

    assert "document.createElement('details')" not in viewer_js
    assert "document.createElement('summary')" not in viewer_js
    assert "axis-item axis-summary" in viewer_js
    assert "axisTreeCollapsedModels" in viewer_js
    assert "const modelOpen = !!searchText || (stableMode ? expandedByUser : !collapsedByUser);" in viewer_js
    assert "const nextOpen = !modelOpen;" in viewer_js
    assert "event.stopPropagation();" in viewer_js
    assert ".axis-model.open > .axis-summary::before" in index_html
    assert "font-size: 12px;" in index_html
    assert "font-size: var(--gui-font-size);" in index_html


def test_zoom_fit_bounds_ignore_root_origin_helper_points() -> None:
    viewer_js = (ASSET_DIR / "viewer.js").read_text(encoding="utf-8")

    assert "function isRootOriginNode" in viewer_js
    assert "return bounds || fallbackBounds;" in viewer_js
    assert "if (!isRootOriginNode(nodeName, pos))" in viewer_js
    assert "bounds = mergeBounds(bounds, minV);" not in viewer_js
    assert "bounds = mergeBounds(bounds, maxV);" not in viewer_js


def test_detach_is_recoverable_when_next_payload_arrives() -> None:
    viewer_js = (ASSET_DIR / "viewer.js").read_text(encoding="utf-8")

    assert "detached: false," in viewer_js
    assert "function resumeFromDetach()" in viewer_js
    assert "resumeFromDetach();\n    state.pendingPayload = payload;" in viewer_js
    assert "if (!state.running) {\n      setRunning(true);" in viewer_js
    assert "state.detached = true;" in viewer_js
    assert "state.detached = false;" in viewer_js
    assert "window.removeEventListener('keydown', onKeyDown);" not in viewer_js
    assert "window.removeEventListener('keyup', onKeyUp);" not in viewer_js
    assert "resizeObserver.disconnect()" not in viewer_js

from __future__ import annotations

from pathlib import Path

from f8pystudio.editor_assist.pyscript_stubs import write_support_files


def test_write_support_files_writes_protocol_payload_files(tmp_path: Path) -> None:
    overlay = write_support_files(
        tmp_path,
        support_files=(
            ("f8_script_api.pyi", "class F8PyScriptContext:\n    ...\n"),
            ("support/extra.pyi", "class Extra:\n    ...\n"),
        ),
        overlay_prefix="from f8_script_api import F8PyScriptContext\n",
    )
    assert overlay == "from f8_script_api import F8PyScriptContext\n"
    assert (tmp_path / "f8_script_api.pyi").read_text(encoding="utf-8").startswith("class F8PyScriptContext:")
    assert (tmp_path / "support" / "extra.pyi").read_text(encoding="utf-8").startswith("class Extra:")


def test_write_support_files_rejects_parent_path_escape(tmp_path: Path) -> None:
    _ = write_support_files(
        tmp_path,
        support_files=(("../escape.pyi", "bad"), ("ok.pyi", "good")),
        overlay_prefix="",
    )
    assert not (tmp_path.parent / "escape.pyi").exists()
    assert (tmp_path / "ok.pyi").read_text(encoding="utf-8") == "good"

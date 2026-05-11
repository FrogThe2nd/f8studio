from __future__ import annotations

import io
import tarfile

from f8pystudio.ui.support.ai_assist_page import build_ai_assist_html
from f8pystudio.ui.support.monaco_editor_page import MonacoEditorPageConfig, build_monaco_editor_html
from f8pystudio.ui.support import web_asset_utils
from f8pystudio.ui.support.web_asset_utils import (
    ensure_web_assets_installed,
    render_prism_asset_html,
    resolve_monaco_base_url,
    resolve_web_asset_page_base_url,
)


def _write_tar_gz(path, files: dict[str, str]) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=str(name))
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_resolve_monaco_base_url_prefers_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("F8_MONACO_BASE_URL", "https://example.invalid/monaco/min")
    monkeypatch.delenv("F8_WEB_ASSETS_DIR", raising=False)

    resolved = resolve_monaco_base_url()

    assert resolved == "https://example.invalid/monaco/min"


def test_resolve_monaco_base_url_uses_local_asset_root(monkeypatch, tmp_path) -> None:
    local_root = tmp_path / "web-assets"
    loader_path = local_root / "monaco" / "min" / "vs" / "loader.js"
    loader_path.parent.mkdir(parents=True, exist_ok=True)
    loader_path.write_text("// loader", encoding="utf-8")
    monkeypatch.delenv("F8_MONACO_BASE_URL", raising=False)
    monkeypatch.setenv("F8_WEB_ASSETS_DIR", str(local_root))

    resolved = resolve_monaco_base_url()

    assert resolved == (local_root / "monaco" / "min").resolve().as_uri()


def test_render_prism_asset_html_uses_local_assets(monkeypatch, tmp_path) -> None:
    local_root = tmp_path / "web-assets"
    prism_dir = local_root / "prism"
    components_dir = prism_dir / "components"
    components_dir.mkdir(parents=True, exist_ok=True)
    (prism_dir / "prism-twilight.min.css").write_text("/* css */", encoding="utf-8")
    (prism_dir / "prism.min.js").write_text("// core", encoding="utf-8")
    (components_dir / "prism-python.min.js").write_text("// py", encoding="utf-8")
    monkeypatch.setenv("F8_WEB_ASSETS_DIR", str(local_root))

    html = render_prism_asset_html(languages=("python", "json"))

    assert "prism-twilight.min.css" in html
    assert "prism.min.js" in html
    assert "prism-python.min.js" in html
    assert "prism-json.min.js" not in html
    assert "cdnjs.cloudflare.com" not in html


def test_build_ai_assist_html_omits_remote_prism_tags_by_default() -> None:
    html = build_ai_assist_html()

    assert "cdnjs.cloudflare.com" not in html
    assert "qrc:///qtwebchannel/qwebchannel.js" in html


def test_build_monaco_editor_html_omits_remote_prism_tags_by_default() -> None:
    html = build_monaco_editor_html(
        MonacoEditorPageConfig(
            code="print('hello')\n",
            language="python",
            monaco_base_url="https://example.invalid/monaco/min",
            python_assist_enabled=True,
        )
    )

    assert "cdnjs.cloudflare.com" not in html
    assert 'src="vs/loader.js"' in html
    assert "paths: { 'vs': 'vs' }" in html


def test_build_monaco_editor_html_maps_angelscript_to_cpp_highlighting() -> None:
    html = build_monaco_editor_html(
        MonacoEditorPageConfig(
            code="// comment\nstring on_exec_json() { return \"\"; }\n",
            language="angelscript",
            monaco_base_url="https://example.invalid/monaco/min",
        )
    )

    assert "if (_languageRegistered('cpp')) return 'cpp';" in html
    assert "language === 'angelscript' ? 'cpp' : language" in html
    comment_rule = r"[/\/\/.*$/, 'comment']"
    assert comment_rule in html
    assert "[/@symbols/" in html
    assert html.index(comment_rule) < html.index("[/@symbols/")


def test_resolve_web_asset_page_base_url_prefers_configured_root(monkeypatch, tmp_path) -> None:
    local_root = tmp_path / "web-assets"
    monkeypatch.setenv("F8_WEB_ASSETS_DIR", str(local_root))

    resolved = resolve_web_asset_page_base_url()

    assert resolved == f"{local_root.resolve().as_uri()}/"


def test_ensure_web_assets_installed_populates_app_data_root(monkeypatch, tmp_path) -> None:
    app_data_root = tmp_path / "appdata" / "web_assets"
    download_root = tmp_path / "downloads"
    download_root.mkdir(parents=True, exist_ok=True)
    monaco_archive = download_root / "monaco.tgz"
    prism_archive = download_root / "prism.tgz"
    _write_tar_gz(
        monaco_archive,
        {
            "package/min/vs/loader.js": "// loader",
            "package/min/vs/editor/editor.main.js": "// editor",
        },
    )
    _write_tar_gz(
        prism_archive,
        {
            "package/prism.js": "// prism core",
            "package/themes/prism-twilight.min.css": "/* css */",
            "package/components/prism-python.min.js": "// py",
            "package/components/prism-javascript.min.js": "// js",
            "package/components/prism-bash.min.js": "// bash",
            "package/components/prism-json.min.js": "// json",
            "package/components/prism-lua.min.js": "// lua",
            "package/components/prism-cpp.min.js": "// cpp",
            "package/components/prism-c.min.js": "// c",
        },
    )

    def _fake_download(url: str, target_path, *, user_agent: str) -> None:
        _ = user_agent
        if "monaco-editor" in url:
            target_path.write_bytes(monaco_archive.read_bytes())
            return
        if "prismjs" in url:
            target_path.write_bytes(prism_archive.read_bytes())
            return
        raise AssertionError(f"unexpected download url: {url}")

    monkeypatch.delenv("F8_MONACO_BASE_URL", raising=False)
    monkeypatch.delenv("F8_WEB_ASSETS_DIR", raising=False)
    monkeypatch.setattr("f8pystudio.ui.support.web_asset_utils._app_data_web_asset_root", lambda: app_data_root)
    monkeypatch.setattr("f8pystudio.ui.support.web_asset_utils._download_url_to_file", _fake_download)

    assert ensure_web_assets_installed() is True
    assert (app_data_root / "monaco" / "min" / "vs" / "loader.js").is_file()
    assert (app_data_root / "prism" / "prism.js").is_file()
    assert (app_data_root / "prism" / "components" / "prism-python.min.js").is_file()
    assert (app_data_root / "prism" / "components" / "prism-lua.min.js").is_file()
    assert (app_data_root / "prism" / "components" / "prism-cpp.min.js").is_file()
    assert resolve_monaco_base_url() == (app_data_root / "monaco" / "min").resolve().as_uri()

    prism_html = render_prism_asset_html(languages=("python", "json", "lua", "cpp"))
    assert "file://" in prism_html
    assert "prism-python.min.js" in prism_html
    assert "prism-json.min.js" in prism_html
    assert "prism-lua.min.js" in prism_html
    assert "prism-cpp.min.js" in prism_html


def test_resolve_functions_schedule_bootstrap_when_assets_missing(monkeypatch, tmp_path) -> None:
    app_data_root = tmp_path / "missing-assets"
    scheduled_calls: list[str] = []

    monkeypatch.delenv("F8_MONACO_BASE_URL", raising=False)
    monkeypatch.delenv("F8_WEB_ASSETS_DIR", raising=False)
    monkeypatch.setattr("f8pystudio.ui.support.web_asset_utils._app_data_web_asset_root", lambda: app_data_root)
    monkeypatch.setattr("f8pystudio.ui.support.web_asset_utils.schedule_web_asset_bootstrap", lambda: scheduled_calls.append("scheduled"))

    monaco_base_url = resolve_monaco_base_url()
    prism_assets = web_asset_utils.resolve_prism_asset_urls(languages=("python",))

    assert monaco_base_url == "https://cdn.jsdelivr.net/npm/monaco-editor/min"
    assert prism_assets.script_urls == ()
    assert scheduled_calls == ["scheduled", "scheduled"]

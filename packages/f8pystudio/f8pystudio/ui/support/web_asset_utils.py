from __future__ import annotations

from dataclasses import dataclass
import html
import json
import logging
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import tarfile
import tempfile
import threading
import time
from typing import Callable
import urllib.request

from qtpy import QtCore

logger = logging.getLogger(__name__)

_WEB_ASSET_ROOT_ENV = "F8_WEB_ASSETS_DIR"
_MONACO_BASE_URL_ENV = "F8_MONACO_BASE_URL"
_REMOTE_MONACO_BASE_URL = "https://cdn.jsdelivr.net/npm/monaco-editor/min"
_WEB_ASSET_SCHEMA_VERSION = 1
_MONACO_NPM_VERSION = "0.55.1"
_PRISM_NPM_VERSION = "1.30.0"
_MONACO_TARBALL_URL = f"https://registry.npmjs.org/monaco-editor/-/monaco-editor-{_MONACO_NPM_VERSION}.tgz"
_PRISM_TARBALL_URL = f"https://registry.npmjs.org/prismjs/-/prismjs-{_PRISM_NPM_VERSION}.tgz"
_PRISM_LANGUAGES = ("python", "javascript", "bash", "json", "lua", "cpp", "c")
_WEB_ASSET_MANIFEST_FILENAME = "manifest.json"
_WEB_ASSET_LOCK_FILENAME = ".install.lock"
_WEB_ASSET_BOOTSTRAP_TIMEOUT_S = 60.0
_WEB_ASSET_WAIT_TIMEOUT_S = 30.0
_WEB_ASSET_POLL_INTERVAL_S = 0.2
_WEB_ASSET_BOOTSTRAP_THREAD_STARTED = False
_WEB_ASSET_BOOTSTRAP_THREAD_LOCK = threading.Lock()


@dataclass(frozen=True)
class PrismAssetUrls:
    stylesheet_url: str | None
    script_urls: tuple[str, ...]


@dataclass(frozen=True)
class WebAssetManifest:
    schema_version: int
    monaco_version: str
    prism_version: str


_CURRENT_WEB_ASSET_MANIFEST = WebAssetManifest(
    schema_version=_WEB_ASSET_SCHEMA_VERSION,
    monaco_version=_MONACO_NPM_VERSION,
    prism_version=_PRISM_NPM_VERSION,
)


def _configured_web_asset_root() -> Path | None:
    raw_root = str(os.environ.get(_WEB_ASSET_ROOT_ENV) or "").strip()
    if not raw_root:
        return None
    return Path(raw_root).expanduser()


def _app_data_web_asset_root() -> Path | None:
    app_data_dir = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppDataLocation)
    if not app_data_dir:
        return None
    return Path(app_data_dir) / "web_assets"


def web_asset_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    configured_root = _configured_web_asset_root()
    if configured_root is not None:
        roots.append(configured_root)

    app_data_root = _app_data_web_asset_root()
    if app_data_root is not None and app_data_root not in roots:
        roots.append(app_data_root)

    return tuple(roots)


def _path_uri(path: Path) -> str:
    return path.resolve().as_uri()


def resolve_web_asset_page_base_url() -> str | None:
    configured_root = _configured_web_asset_root()
    if configured_root is not None:
        return _path_uri(configured_root) + "/"

    app_data_root = _app_data_web_asset_root()
    if app_data_root is None:
        return None
    return _path_uri(app_data_root) + "/"


def _manifest_path(root: Path) -> Path:
    return root / _WEB_ASSET_MANIFEST_FILENAME


def _lock_path(root: Path) -> Path:
    return root.parent / f"{root.name}{_WEB_ASSET_LOCK_FILENAME}"


def _manifest_to_dict(manifest: WebAssetManifest) -> dict[str, object]:
    return {
        "schemaVersion": int(manifest.schema_version),
        "monacoVersion": str(manifest.monaco_version),
        "prismVersion": str(manifest.prism_version),
    }


def _manifest_from_dict(payload: dict[str, object]) -> WebAssetManifest | None:
    try:
        schema_version = int(payload["schemaVersion"])
        monaco_version = str(payload["monacoVersion"])
        prism_version = str(payload["prismVersion"])
    except (KeyError, TypeError, ValueError):
        return None
    return WebAssetManifest(
        schema_version=schema_version,
        monaco_version=monaco_version,
        prism_version=prism_version,
    )


def _required_monaco_files(root: Path) -> tuple[Path, ...]:
    return (
        root / "monaco" / "min" / "vs" / "loader.js",
        root / "monaco" / "min" / "vs" / "editor" / "editor.main.js",
    )


def _required_prism_files(root: Path) -> tuple[Path, ...]:
    files = [
        root / "prism" / "prism.js",
        root / "prism" / "prism-twilight.min.css",
    ]
    for language in _PRISM_LANGUAGES:
        files.append(root / "prism" / "components" / f"prism-{language}.min.js")
    return tuple(files)


def _read_manifest(root: Path) -> WebAssetManifest | None:
    manifest_path = _manifest_path(root)
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("failed to read web asset manifest from %s", manifest_path)
        return None
    if not isinstance(payload, dict):
        return None
    return _manifest_from_dict(payload)


def _write_manifest(root: Path, manifest: WebAssetManifest) -> None:
    manifest_path = _manifest_path(root)
    payload = _manifest_to_dict(manifest)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _web_assets_ready(root: Path) -> bool:
    manifest = _read_manifest(root)
    if manifest != _CURRENT_WEB_ASSET_MANIFEST:
        return False
    for required_file in (*_required_monaco_files(root), *_required_prism_files(root)):
        if not required_file.is_file():
            return False
    return True


def _log(log_cb: Callable[[str], None] | None, message: str) -> None:
    if log_cb is None:
        logger.info("%s", str(message))
        return
    log_cb(str(message))


def _download_url_to_file(url: str, target_path: Path, *, user_agent: str) -> None:
    request = urllib.request.Request(
        str(url),
        headers={
            "User-Agent": str(user_agent),
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=_WEB_ASSET_BOOTSTRAP_TIMEOUT_S) as response, target_path.open("wb") as output_file:
        shutil.copyfileobj(response, output_file)


def _extract_tar_directory(archive_path: Path, *, source_prefix: str, target_root: Path) -> None:
    source_root = PurePosixPath(source_prefix)
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            member_path = PurePosixPath(member.name)
            try:
                relative_path = member_path.relative_to(source_root)
            except ValueError:
                continue
            destination_path = target_root.joinpath(*relative_path.parts)
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            source_file = archive.extractfile(member)
            if source_file is None:
                continue
            with source_file, destination_path.open("wb") as output_file:
                shutil.copyfileobj(source_file, output_file)


def _extract_tar_file(archive_path: Path, *, source_name: str, target_path: Path) -> None:
    with tarfile.open(archive_path, mode="r:gz") as archive:
        member = archive.getmember(source_name)
        source_file = archive.extractfile(member)
        if source_file is None:
            raise RuntimeError(f"failed to extract tar member {source_name!r}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with source_file, target_path.open("wb") as output_file:
            shutil.copyfileobj(source_file, output_file)


def _install_web_assets_from_archives(*, root: Path, monaco_archive: Path, prism_archive: Path, log_cb: Callable[[str], None] | None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="f8-web-assets-", dir=root.parent if root.parent.exists() else None) as temp_dir:
        staging_root = Path(temp_dir) / "web_assets"
        monaco_root = staging_root / "monaco" / "min"
        prism_root = staging_root / "prism"
        prism_components_root = prism_root / "components"

        _log(log_cb, "Web assets bootstrap: extracting Monaco editor")
        _extract_tar_directory(monaco_archive, source_prefix="package/min", target_root=monaco_root)

        _log(log_cb, "Web assets bootstrap: extracting Prism assets")
        _extract_tar_file(prism_archive, source_name="package/prism.js", target_path=prism_root / "prism.js")
        _extract_tar_file(
            prism_archive,
            source_name="package/themes/prism-twilight.min.css",
            target_path=prism_root / "prism-twilight.min.css",
        )
        for language in _PRISM_LANGUAGES:
            _extract_tar_file(
                prism_archive,
                source_name=f"package/components/prism-{language}.min.js",
                target_path=prism_components_root / f"prism-{language}.min.js",
            )

        if root.is_dir():
            shutil.rmtree(root)
        shutil.copytree(staging_root, root)
    _write_manifest(root, _CURRENT_WEB_ASSET_MANIFEST)


def _download_and_install_web_assets(*, root: Path, log_cb: Callable[[str], None] | None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="f8-web-assets-download-") as temp_dir:
        temp_root = Path(temp_dir)
        monaco_archive = temp_root / f"monaco-editor-{_MONACO_NPM_VERSION}.tgz"
        prism_archive = temp_root / f"prismjs-{_PRISM_NPM_VERSION}.tgz"
        _log(log_cb, f"Web assets bootstrap: downloading Monaco {_MONACO_NPM_VERSION}")
        _download_url_to_file(_MONACO_TARBALL_URL, monaco_archive, user_agent="f8pystudio-web-assets")
        _log(log_cb, f"Web assets bootstrap: downloading Prism {_PRISM_NPM_VERSION}")
        _download_url_to_file(_PRISM_TARBALL_URL, prism_archive, user_agent="f8pystudio-web-assets")
        _install_web_assets_from_archives(
            root=root,
            monaco_archive=monaco_archive,
            prism_archive=prism_archive,
            log_cb=log_cb,
        )


def _try_acquire_lock(lock_path: Path) -> int | None:
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


def _release_lock(lock_path: Path, fd: int) -> None:
    os.close(fd)
    try:
        lock_path.unlink()
    except FileNotFoundError:
        return


def _wait_for_web_assets(root: Path, *, timeout_s: float) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while time.monotonic() < deadline:
        if _web_assets_ready(root):
            return True
        time.sleep(_WEB_ASSET_POLL_INTERVAL_S)
    return _web_assets_ready(root)


def ensure_web_assets_installed(*, log_cb: Callable[[str], None] | None = None) -> bool:
    configured_root = _configured_web_asset_root()
    if configured_root is not None:
        return _web_assets_ready(configured_root)

    root = _app_data_web_asset_root()
    if root is None:
        return False
    if _web_assets_ready(root):
        return True

    root.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(root)
    lock_fd = _try_acquire_lock(lock_path)
    if lock_fd is None:
        _log(log_cb, f"Web assets bootstrap: waiting for active installer at {lock_path}")
        return _wait_for_web_assets(root, timeout_s=_WEB_ASSET_WAIT_TIMEOUT_S)

    try:
        if _web_assets_ready(root):
            return True
        _download_and_install_web_assets(root=root, log_cb=log_cb)
        return _web_assets_ready(root)
    finally:
        _release_lock(lock_path, lock_fd)


def _run_bootstrap_thread() -> None:
    try:
        ensure_web_assets_installed()
    except Exception:
        logger.exception("failed to bootstrap local web assets")
    finally:
        global _WEB_ASSET_BOOTSTRAP_THREAD_STARTED
        with _WEB_ASSET_BOOTSTRAP_THREAD_LOCK:
            _WEB_ASSET_BOOTSTRAP_THREAD_STARTED = False


def schedule_web_asset_bootstrap() -> None:
    configured_root = _configured_web_asset_root()
    if configured_root is not None:
        return
    root = _app_data_web_asset_root()
    if root is None or _web_assets_ready(root):
        return

    global _WEB_ASSET_BOOTSTRAP_THREAD_STARTED
    with _WEB_ASSET_BOOTSTRAP_THREAD_LOCK:
        if _WEB_ASSET_BOOTSTRAP_THREAD_STARTED:
            return
        _WEB_ASSET_BOOTSTRAP_THREAD_STARTED = True

    bootstrap_thread = threading.Thread(
        target=_run_bootstrap_thread,
        name="f8pystudio-web-assets",
        daemon=True,
    )
    bootstrap_thread.start()


def resolve_monaco_base_url() -> str:
    explicit_base_url = str(os.environ.get(_MONACO_BASE_URL_ENV) or "").strip().rstrip("/")
    if explicit_base_url:
        return explicit_base_url

    for root in web_asset_roots():
        monaco_base_dir = root / "monaco" / "min"
        if (monaco_base_dir / "vs" / "loader.js").is_file():
            return _path_uri(monaco_base_dir)

    schedule_web_asset_bootstrap()
    logger.debug("Monaco local assets unavailable; falling back to remote base URL")
    return _REMOTE_MONACO_BASE_URL


def resolve_prism_asset_urls(*, languages: tuple[str, ...]) -> PrismAssetUrls:
    normalized_languages = tuple(
        dict.fromkeys(
            str(language or "").strip().lower()
            for language in languages
            if str(language or "").strip()
        )
    )

    for root in web_asset_roots():
        prism_dir = root / "prism"
        core_script = prism_dir / "prism.min.js"
        if not core_script.is_file():
            core_script = prism_dir / "prism.js"
        if not core_script.is_file():
            continue

        stylesheet_path = prism_dir / "prism-twilight.min.css"
        stylesheet_url = _path_uri(stylesheet_path) if stylesheet_path.is_file() else None
        script_urls = [_path_uri(core_script)]
        for language in normalized_languages:
            component_path = prism_dir / "components" / f"prism-{language}.min.js"
            if component_path.is_file():
                script_urls.append(_path_uri(component_path))
        return PrismAssetUrls(
            stylesheet_url=stylesheet_url,
            script_urls=tuple(script_urls),
        )

    schedule_web_asset_bootstrap()
    return PrismAssetUrls(stylesheet_url=None, script_urls=())


def render_prism_asset_html(*, languages: tuple[str, ...]) -> str:
    prism_assets = resolve_prism_asset_urls(languages=languages)
    tags: list[str] = []
    if prism_assets.stylesheet_url:
        stylesheet_url = html.escape(prism_assets.stylesheet_url, quote=True)
        tags.append(f'<link href="{stylesheet_url}" rel="stylesheet" />')
    for script_url in prism_assets.script_urls:
        escaped_url = html.escape(script_url, quote=True)
        tags.append(f'<script src="{escaped_url}"></script>')
    return "\n    ".join(tags)

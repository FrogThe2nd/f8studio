import os
import sys
from pathlib import Path

PKG_PYDL = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PKG_PYDL not in sys.path:
    sys.path.insert(0, PKG_PYDL)

from f8pydl import service_paths  # noqa: E402


def test_resolve_path_from_cwd_or_repo_prefers_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    local_file = tmp_path / "models" / "local.yaml"
    local_file.parent.mkdir()
    local_file.write_text("x", encoding="utf-8")

    resolved = service_paths.resolve_path_from_cwd_or_repo("models/local.yaml")

    assert resolved == local_file.resolve()


def test_resolve_path_from_cwd_or_repo_falls_back_to_repo_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    repo_root = tmp_path / "repo"
    repo_relative = Path("services") / "f8" / "dl"
    repo_path = repo_root / repo_relative
    repo_path.mkdir(parents=True, exist_ok=True)
    marker = repo_path / "service_paths_test_marker.yaml"
    marker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(service_paths, "package_root", lambda: repo_root)

    resolved = service_paths.resolve_path_from_cwd_or_repo(str(repo_relative / marker.name))

    assert resolved == marker.resolve()


def test_default_weights_dir_uses_extra_candidate_when_base_dirs_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    repo_root = tmp_path / "repo"
    extra_relative = Path("services") / "f8" / "detect_tracker" / "weights"
    extra_dir = repo_root / extra_relative
    extra_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(service_paths, "package_root", lambda: repo_root)

    resolved = service_paths.default_weights_dir(extra_relative_candidates=(extra_relative.as_posix(),))

    assert resolved == extra_dir.resolve()

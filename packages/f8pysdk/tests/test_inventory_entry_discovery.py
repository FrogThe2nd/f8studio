from __future__ import annotations

from pathlib import Path
from typing import Any

from f8pysdk.codec import dump_json
from f8pysdk.service_runtime_tools.inventory.entry import find_service_dirs, load_service_entry


def test_find_service_dirs_discovers_nested_service_files(tmp_path: Path) -> None:
    root = tmp_path / "services"
    alpha = root / "f8" / "alpha"
    beta = root / "f8" / "beta"
    gamma = root / "f8" / "gamma"
    nested = root / "f8" / "group" / "nested"
    for service_dir, filename in (
        (alpha, "service.yml"),
        (beta, "service.linux.yml"),
        (gamma, "service.mac.yml"),
        (nested, "service.win.yml"),
    ):
        service_dir.mkdir(parents=True)
        (service_dir / filename).write_text("launch:\n  command: echo\n", encoding="utf-8")
    (root / "f8" / "not_service").mkdir(parents=True)
    (root / "f8" / "not_service" / "other.yml").write_text("{}\n", encoding="utf-8")

    found = find_service_dirs([root])

    assert found == sorted([alpha.resolve(), beta.resolve(), gamma.resolve(), nested.resolve()])


def test_find_service_dirs_ignores_missing_roots(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    assert find_service_dirs([missing]) == []


def _write_entry(service_dir: Path, *, workdir: str = "./", command: str = "runner") -> None:
    service_dir.mkdir(parents=True)
    (service_dir / "service.yml").write_text(
        "schemaVersion: f8serviceEntry/1\n"
        "serviceClass: f8.tests.entry\n"
        "label: Entry\n"
        "version: 0.0.1\n"
        "launch:\n"
        f"  command: {command}\n"
        "  args: []\n"
        "  env: {}\n"
        f"  workdir: {workdir}\n",
        encoding="utf-8",
    )


def test_load_service_entry_matches_for_relative_and_absolute_service_dir(tmp_path: Path, monkeypatch: Any) -> None:
    root = tmp_path / "services"
    service_dir = root / "f8" / "entry"
    _write_entry(service_dir, workdir="../entry", command="runner")
    monkeypatch.chdir(tmp_path)

    relative_payload = dump_json(load_service_entry(Path("services/f8/entry")), mode="json")
    absolute_payload = dump_json(load_service_entry(service_dir.resolve()), mode="json")

    assert relative_payload == absolute_payload
    assert relative_payload["launch"]["workdir"] == str(service_dir.resolve())

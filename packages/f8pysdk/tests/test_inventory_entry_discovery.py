from __future__ import annotations

from pathlib import Path

from f8pysdk.service_runtime_tools.inventory.entry import find_service_dirs


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

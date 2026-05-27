from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from f8pysdk.codec import dump_json
from f8pysdk.service_runtime_tools.inventory.catalog import ServiceCatalog
from f8pysdk.service_runtime_tools.inventory.discovery import load_discovery_into_catalog
from f8pysdk.service_runtime_tools.inventory.entry import find_service_dirs, load_service_entry
from f8pysdk.service_runtime_tools.inventory.policy import (
    SERVICE_DISCOVERY_POLICY_ENV,
    ServiceDiscoveryPolicy,
    load_default_service_discovery_policy,
    load_service_discovery_policy,
    merge_disabled_service_classes,
)


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


def test_find_service_dirs_logs_recursive_scan_fallback(tmp_path: Path, monkeypatch: Any, caplog: pytest.LogCaptureFixture) -> None:
    root = tmp_path / "services"
    service_dir = root / "alpha"
    service_dir.mkdir(parents=True)
    (service_dir / "service.yml").write_text("launch:\n  command: echo\n", encoding="utf-8")
    original_rglob = Path.rglob

    def _failing_rglob(path: Path, pattern: str) -> Any:
        if path == root:
            raise OSError("scan failed")
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", _failing_rglob)

    caplog.set_level("DEBUG", logger="f8pysdk.service_runtime_tools.inventory.entry")
    found = find_service_dirs([root])

    assert found == [service_dir.resolve()]
    assert "recursive service discovery failed" in caplog.text


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


def _write_discoverable_service(service_dir: Path, *, service_class: str) -> None:
    service_dir.mkdir(parents=True)
    (service_dir / "service.yml").write_text(
        "schemaVersion: f8serviceEntry/1\n"
        f"serviceClass: {service_class}\n"
        "label: Entry\n"
        "version: 0.0.1\n"
        "launch:\n"
        "  command: runner\n"
        "  args: []\n"
        "  env: {}\n"
        "  workdir: ./\n",
        encoding="utf-8",
    )
    (service_dir / "describe.json").write_text(
        "{\n"
        '  "service": {\n'
        '    "schemaVersion": "f8service/1",\n'
        f'    "serviceClass": "{service_class}",\n'
        '    "label": "Entry",\n'
        '    "version": "0.0.1"\n'
        "  },\n"
        '  "operators": []\n'
        "}\n",
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


def test_load_service_entry_logs_platform_candidate_path_failure(
    tmp_path: Path,
    monkeypatch: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service_dir = tmp_path / "services" / "f8" / "entry"
    service_dir.mkdir(parents=True)
    (service_dir / "service.linux.yml").write_text(
        "schemaVersion: f8serviceEntry/1\n"
        "serviceClass: f8.tests.platform\n"
        "launch:\n"
        "  command: ./runner.py\n"
        "  workdir: ./\n",
        encoding="utf-8",
    )
    (service_dir / "service.yml").write_text(
        "schemaVersion: f8serviceEntry/1\n"
        "serviceClass: f8.tests.fallback\n"
        "launch:\n"
        "  command: runner\n"
        "  workdir: ./\n",
        encoding="utf-8",
    )
    original_resolve = Path.resolve

    def _failing_resolve(path: Path, *args: Any, **kwargs: Any) -> Path:
        if path.name == "runner.py":
            raise OSError("resolve failed")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _failing_resolve)

    caplog.set_level("DEBUG", logger="f8pysdk.service_runtime_tools.inventory.entry")
    entry = load_service_entry(service_dir)

    assert str(entry.serviceClass) == "f8.tests.platform"
    assert "platform service entry command probe failed" in caplog.text


def test_load_discovery_into_catalog_skips_disabled_service_class(tmp_path: Path) -> None:
    root = tmp_path / "services"
    disabled_dir = root / "f8" / "disabled"
    enabled_dir = root / "f8" / "enabled"
    _write_discoverable_service(disabled_dir, service_class="f8.tests.disabled")
    _write_discoverable_service(enabled_dir, service_class="f8.tests.enabled")
    catalog = ServiceCatalog()
    catalog.clear()

    try:
        found = load_discovery_into_catalog(
            roots=[root],
            catalog=catalog,
            disabled_service_classes=("f8.tests.disabled",),
        )

        assert found == ["f8.tests.enabled"]
        assert not catalog.services.has("f8.tests.disabled")
        assert catalog.services.has("f8.tests.enabled")
    finally:
        catalog.clear()


def test_load_service_discovery_policy_reads_disabled_service_classes(tmp_path: Path) -> None:
    policy_path = tmp_path / "service_discovery_policy.yml"
    policy_path.write_text(
        "schemaVersion: f8serviceDiscoveryPolicy/1\n"
        "disabledServiceClasses:\n"
        "  - f8.cppengine\n"
        "  - f8.tests.experimental\n",
        encoding="utf-8",
    )

    policy = load_service_discovery_policy(policy_path)

    assert policy.disabled_service_classes == ("f8.cppengine", "f8.tests.experimental")


def test_load_default_service_discovery_policy_uses_env_path(tmp_path: Path, monkeypatch: Any) -> None:
    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(
        "schemaVersion: f8serviceDiscoveryPolicy/1\n"
        "disabledServiceClasses:\n"
        "  - f8.cppengine\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(SERVICE_DISCOVERY_POLICY_ENV, str(policy_path))

    policy = load_default_service_discovery_policy(start_path=tmp_path)

    assert policy.disabled_service_classes == ("f8.cppengine",)


def test_merge_disabled_service_classes_dedupes_policy_explicit_and_env(monkeypatch: Any) -> None:
    policy_path_value = "f8.policy,f8.shared"
    monkeypatch.setenv("F8_DISABLED_SERVICE_CLASSES", f"{policy_path_value}{os.pathsep}f8.env")

    merged = merge_disabled_service_classes(
        policy=ServiceDiscoveryPolicy(disabled_service_classes=("f8.policy",)),
        explicit_service_classes=("f8.cli", "f8.shared"),
        include_env=True,
    )

    assert merged == ("f8.policy", "f8.cli", "f8.shared", "f8.env")

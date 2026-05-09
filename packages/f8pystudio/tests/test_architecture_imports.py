from __future__ import annotations

import ast
import importlib
from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = TESTS_ROOT.parent / "f8pystudio"
DOMAIN_ASSETS_ROOTS = (
    PACKAGE_ROOT / "assets" / "common",
    PACKAGE_ROOT / "assets" / "components",
    PACKAGE_ROOT / "assets" / "db",
    PACKAGE_ROOT / "assets" / "projects",
    PACKAGE_ROOT / "assets" / "variants",
)
LOW_LEVEL_UI_ROOTS = (
    PACKAGE_ROOT / "ui" / "components",
    PACKAGE_ROOT / "ui" / "dialogs",
    PACKAGE_ROOT / "ui" / "support",
    PACKAGE_ROOT / "ui" / "widgets",
)
LIGHTWEIGHT_CORE_ROOTS = (
    PACKAGE_ROOT / "contracts",
    PACKAGE_ROOT / "diagnostics",
    PACKAGE_ROOT / "studio_specs",
    PACKAGE_ROOT / "visualization",
)
ASSETS_ROOT = PACKAGE_ROOT / "assets"


def _package_module_for_path(path: Path) -> tuple[str, ...]:
    return path.relative_to(PACKAGE_ROOT).with_suffix("").parts


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _is_type_checking_only(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, ast.If):
            test = parent.test
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                return True
        parent = parents.get(parent)
    return False


def _import_targets(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = _parent_map(tree)
    package_parts = _package_module_for_path(path)[:-1]
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if _is_type_checking_only(node, parents):
                continue
            for alias in node.names:
                targets.add(str(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if _is_type_checking_only(node, parents):
                continue
            if node.level:
                keep = len(package_parts) - (node.level - 1)
                if keep < 0:
                    keep = 0
                prefix = ("f8pystudio",) + package_parts[:keep]
                if node.module:
                    targets.add(".".join(prefix + tuple(node.module.split("."))))
                else:
                    targets.add(".".join(prefix))
            elif node.module:
                targets.add(str(node.module))
    return targets


def test_root_python_files_are_only_entrypoints() -> None:
    root_py_files = sorted(path.name for path in PACKAGE_ROOT.glob("*.py"))
    assert root_py_files == ["__init__.py", "main.py"]


def test_nodegraph_init_has_no_dynamic_getattr_export() -> None:
    source = (PACKAGE_ROOT / "nodegraph" / "__init__.py").read_text(encoding="utf-8")
    assert "__getattr__" not in source


def test_lightweight_core_packages_avoid_runtime_qt_imports() -> None:
    forbidden_prefixes = ("qtpy", "PySide6", "PyQt6", "PyQt5")
    for root in LIGHTWEIGHT_CORE_ROOTS:
        for path in _python_files(root):
            imports = _import_targets(path)
            assert not any(
                target == prefix or target.startswith(prefix + ".")
                for target in imports
                for prefix in forbidden_prefixes
            ), str(path)


def test_assets_domain_core_only_uses_nodegraph_session_schema() -> None:
    allowed_nodegraph_targets = {
        "f8pystudio.nodegraph.session_payload_sanitizer",
        "f8pystudio.nodegraph.session_schema",
    }
    for root in DOMAIN_ASSETS_ROOTS:
        for path in _python_files(root):
            imports = _import_targets(path)
            nodegraph_targets = {
                target for target in imports if target == "f8pystudio.nodegraph" or target.startswith("f8pystudio.nodegraph.")
            }
            ui_targets = {target for target in imports if target == "f8pystudio.ui" or target.startswith("f8pystudio.ui.")}
            assert not ui_targets, str(path)
            assert nodegraph_targets <= allowed_nodegraph_targets, str(path)


def test_assets_common_remote_infrastructure_stays_domain_agnostic() -> None:
    forbidden_targets = (
        "f8pystudio.assets.components",
        "f8pystudio.assets.variants",
        "f8pystudio.assets.projects",
        "f8pystudio.assets.ui",
    )
    for module_name in ("remote_http.py", "remote_sessions.py", "error_reporting.py"):
        path = PACKAGE_ROOT / "assets" / "common" / module_name
        imports = _import_targets(path)
        for forbidden in forbidden_targets:
            assert all(
                target != forbidden and not target.startswith(forbidden + ".")
                for target in imports
            ), str(path)


def test_assets_sync_clients_use_shared_remote_common_helpers() -> None:
    required_targets = {
        "f8pystudio.assets.common.remote_http",
        "f8pystudio.assets.common.remote_sessions",
    }
    for path in (
        PACKAGE_ROOT / "assets" / "components" / "component_sync.py",
        PACKAGE_ROOT / "assets" / "variants" / "variant_sync.py",
    ):
        imports = _import_targets(path)
        assert required_targets <= imports


def test_assets_catalog_browsers_share_explicit_state_factory() -> None:
    required_targets = {
        "f8pystudio.assets.ui.catalog_browser_state",
        "f8pystudio.assets.ui.catalog_refresh_queue_mixin",
    }
    for path in (
        PACKAGE_ROOT / "assets" / "ui" / "component_catalog_browser.py",
        PACKAGE_ROOT / "assets" / "ui" / "variant_catalog_browser.py",
    ):
        imports = _import_targets(path)
        assert required_targets <= imports


def test_assets_forbid_private_chain_shortcuts_and_dynamic_attribute_probing() -> None:
    forbidden_patterns = (
        "_sync_client._catalog_service",
        "_draft_service._db",
        "_preview_graph._",
        "getattr(",
        "setattr(",
        "hasattr(",
    )
    for path in _python_files(ASSETS_ROOT):
        source = path.read_text(encoding="utf-8")
        assert all(pattern not in source for pattern in forbidden_patterns), str(path)


def test_assets_public_exports_remain_stable() -> None:
    import f8pystudio.assets as assets

    expected_exports = [
        "AssetsDatabase",
        "assets_db_path",
        "ProjectStorageService",
        "ComponentCatalogService",
        "ComponentDraftService",
        "ComponentSyncClient",
        "VariantDraftService",
        "VariantSyncClient",
        "F8ProjectRecord",
        "F8ProjectSummary",
        "F8ProjectVersionSummary",
        "F8ComponentDraftEntry",
        "F8ComponentEntry",
        "F8ComponentRecord",
        "F8ComponentSourceKind",
        "F8ComponentVisibility",
        "F8VariantDraftEntry",
        "F8VariantEntry",
        "F8VariantKind",
        "F8VariantRecord",
        "F8VariantRef",
        "F8VariantSourceKind",
        "F8VariantVisibility",
    ]
    assert assets.__all__ == expected_exports
    for export_name in expected_exports:
        assert hasattr(assets, export_name), export_name


def test_low_level_ui_layers_do_not_reach_into_assets_ui_or_studio_bridge() -> None:
    forbidden_targets = {"f8pystudio.assets.ui", "f8pystudio.bridge.studio_bridge"}
    for root in LOW_LEVEL_UI_ROOTS:
        for path in _python_files(root):
            imports = _import_targets(path)
            for forbidden in forbidden_targets:
                assert all(
                    target != forbidden and not target.startswith(forbidden + ".")
                    for target in imports
                ), str(path)


def test_debug_launchers_live_under_app_package() -> None:
    debug_files = [path for path in _python_files(PACKAGE_ROOT) if path.name.startswith("debug_")]
    app_root = PACKAGE_ROOT / "app"
    assert debug_files, "Expected at least one app debug launcher"
    assert all(path.parent == app_root for path in debug_files), [str(path) for path in debug_files]


def test_app_debug_launchers_import() -> None:
    module_names = [
        "f8pystudio.app.debug_component_catalog",
        "f8pystudio.app.debug_monaco_editor",
        "f8pystudio.app.debug_variant_catalog",
    ]
    for module_name in module_names:
        assert importlib.import_module(module_name) is not None


def test_new_public_module_surfaces_import() -> None:
    module_names = [
        "f8pystudio.plugins.api",
        "f8pystudio.plugins.loader",
        "f8pystudio.bridge.studio_bridge",
        "f8pystudio.contracts.ui_commands",
        "f8pystudio.studio_specs.registry",
        "f8pystudio.nodegraph.session_schema",
    ]
    for module_name in module_names:
        assert importlib.import_module(module_name) is not None


def test_extension_modules_import_with_restructured_core_paths() -> None:
    module_names = [
        "f8pystudio_ext_template_match.plugin",
        "f8pystudio_ext_viz_tcode.plugin",
        "f8pystudio_ext_viz_tcode.operators.viz_tcode",
        "f8pystudio_ext_viz_tcode.render_nodes.viz_tcode",
    ]
    for module_name in module_names:
        assert importlib.import_module(module_name) is not None

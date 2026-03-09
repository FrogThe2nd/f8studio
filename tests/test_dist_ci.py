from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


def _load_dist_ci_module() -> object:
    script_path = Path("scripts/dist_ci.py").resolve()
    spec = importlib.util.spec_from_file_location("dist_ci", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load dist_ci module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DistCiDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_dist_ci_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_pyproject(self, relative_package_dir: str, package_name: str) -> None:
        package_dir = self.root / relative_package_dir
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "pyproject.toml").write_text(
            "[project]\n"
            f'name = "{package_name}"\n'
            'version = "0.1.0"\n',
            encoding="utf-8",
        )

    def test_discover_local_editable_packages_ignores_non_packages_or_non_editable(self) -> None:
        self._write_pyproject("packages/pkg_a", "pkg-a")
        self._write_pyproject("packages/pkg_c", "pkg-c")

        pixi_toml_path = self.root / "pixi.toml"
        pixi_toml_path.write_text(
            "[feature.alpha.pypi-dependencies]\n"
            'pkg-a = { path = "packages/pkg_a", editable = true }\n'
            'pkg-b = { path = "packages/pkg_b", editable = false }\n'
            'outside = { path = "../other/pkg", editable = true }\n'
            'requests = ">=2"\n'
            "\n"
            "[feature.beta.pypi-dependencies]\n"
            'pkg-c = { path = "packages/pkg_c", editable = true }\n',
            encoding="utf-8",
        )

        dependencies = self.module._discover_local_editable_package_dirs(
            pixi_toml_path=pixi_toml_path,
            repo_root=self.root,
        )

        self.assertEqual(
            dependencies,
            {
                "pkg-a": "packages/pkg_a",
                "pkg-c": "packages/pkg_c",
            },
        )

    def test_discover_local_editable_packages_fails_on_duplicate_dependency_name(self) -> None:
        self._write_pyproject("packages/pkg_a", "pkg-a")

        pixi_toml_path = self.root / "pixi.toml"
        pixi_toml_path.write_text(
            "[feature.alpha.pypi-dependencies]\n"
            'pkg-a = { path = "packages/pkg_a", editable = true }\n'
            "\n"
            "[feature.beta.pypi-dependencies]\n"
            'pkg-a = { path = "packages/pkg_a", editable = true }\n',
            encoding="utf-8",
        )

        with self.assertRaises(ValueError) as ctx:
            self.module._discover_local_editable_package_dirs(
                pixi_toml_path=pixi_toml_path,
                repo_root=self.root,
            )

        self.assertIn("Duplicate local editable package dependency 'pkg-a'", str(ctx.exception))

    def test_discover_local_editable_packages_fails_when_directory_missing(self) -> None:
        pixi_toml_path = self.root / "pixi.toml"
        pixi_toml_path.write_text(
            "[feature.alpha.pypi-dependencies]\n"
            'pkg-missing = { path = "packages/pkg_missing", editable = true }\n',
            encoding="utf-8",
        )

        with self.assertRaises(FileNotFoundError) as ctx:
            self.module._discover_local_editable_package_dirs(
                pixi_toml_path=pixi_toml_path,
                repo_root=self.root,
            )

        self.assertIn("Package directory for 'pkg-missing' was not found", str(ctx.exception))

    def test_discover_local_editable_packages_fails_when_pyproject_missing(self) -> None:
        package_dir = self.root / "packages" / "pkg_no_pyproject"
        package_dir.mkdir(parents=True, exist_ok=True)

        pixi_toml_path = self.root / "pixi.toml"
        pixi_toml_path.write_text(
            "[feature.alpha.pypi-dependencies]\n"
            'pkg-no-pyproject = { path = "packages/pkg_no_pyproject", editable = true }\n',
            encoding="utf-8",
        )

        with self.assertRaises(FileNotFoundError) as ctx:
            self.module._discover_local_editable_package_dirs(
                pixi_toml_path=pixi_toml_path,
                repo_root=self.root,
            )

        self.assertIn("pyproject.toml for 'pkg-no-pyproject' was not found", str(ctx.exception))

    def test_find_wheel_for_distribution_normalizes_hyphen_and_underscore(self) -> None:
        wheels_dir = self.root / "wheels"
        wheels_dir.mkdir(parents=True, exist_ok=True)
        wheel_path = wheels_dir / "f8pystudio_ext_template_match-0.1.0-py3-none-any.whl"
        wheel_path.write_text("wheel-content", encoding="utf-8")

        discovered_wheel = self.module._find_wheel_for_distribution(
            wheels_dir,
            "f8pystudio-ext-template-match",
        )

        self.assertEqual(discovered_wheel, wheel_path)

    def test_discover_launcher_runtime_environments_from_marker_feature(self) -> None:
        pixi_toml_path = self.root / "pixi.toml"
        pixi_toml_path.write_text(
            "[environments]\n"
            'default = { features = ["python", "studio", "launcher-runtime"] }\n'
            'onnx = { features = ["python", "onnx", "launcher-runtime"] }\n'
            'ci = { features = ["ci"] }\n',
            encoding="utf-8",
        )

        environments = self.module._discover_launcher_runtime_environments(pixi_toml_path=pixi_toml_path)

        self.assertEqual(environments, ["default", "onnx"])

    def test_discover_launcher_runtime_environments_fails_without_marker(self) -> None:
        pixi_toml_path = self.root / "pixi.toml"
        pixi_toml_path.write_text(
            "[environments]\n"
            'ci = { features = ["ci"] }\n',
            encoding="utf-8",
        )

        with self.assertRaises(ValueError) as ctx:
            self.module._discover_launcher_runtime_environments(pixi_toml_path=pixi_toml_path)

        self.assertIn("launcher-runtime", str(ctx.exception))

    def test_env_install_script_text_installs_only_runtime_environments(self) -> None:
        script_text = self.module._env_install_script_text(["default", "onnx"])

        self.assertIn("pixi install -e default -e onnx", script_text)
        self.assertNotIn("pixi install -a", script_text)


class DistCiCppBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_dist_ci_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_preset_file(self, preset_name: str = "conan-release") -> Path:
        preset_path = self.root / "build" / "Release" / "generators" / "CMakePresets.json"
        preset_path.parent.mkdir(parents=True, exist_ok=True)
        preset_path.write_text(
            "{\n"
            '  "version": 3,\n'
            '  "buildPresets": [{"name": "' + preset_name + '"}],\n'
            '  "configurePresets": [{"name": "' + preset_name + '"}]\n'
            "}\n",
            encoding="utf-8",
        )
        return preset_path

    def test_build_cpp_runtime_uses_aggregate_deploy_target(self) -> None:
        preset_path = self._write_preset_file()
        fallback_path = self.root / "build" / "generators" / "CMakePresets.json"
        recorded_commands: list[list[str]] = []

        def _record_run(command: list[str]) -> None:
            recorded_commands.append(command)

        with (
            mock.patch.object(self.module, "CPP_PRESET_PATH", preset_path),
            mock.patch.object(self.module, "CPP_PRESET_FALLBACK_PATH", fallback_path),
            mock.patch.object(self.module, "_run", side_effect=_record_run),
        ):
            self.module._build_cpp_runtime()

        self.assertGreaterEqual(len(recorded_commands), 2)
        build_command = next(command for command in recorded_commands if "--build" in command)
        target_flag_index = build_command.index("--target")
        self.assertEqual(build_command[target_flag_index + 1], self.module.CPP_DEPLOY_ALL_TARGET)
        self.assertNotIn("f8implayer_service_deploy_runtime", build_command)
        self.assertNotIn("f8cvkit_template_match_service_deploy_runtime", build_command)

    def test_build_cpp_runtime_reports_actionable_error_for_missing_registration(self) -> None:
        preset_path = self._write_preset_file()
        fallback_path = self.root / "build" / "generators" / "CMakePresets.json"

        def _fake_run(command: list[str]) -> None:
            if "--build" in command:
                raise subprocess.CalledProcessError(returncode=1, cmd=command)

        with (
            mock.patch.object(self.module, "CPP_PRESET_PATH", preset_path),
            mock.patch.object(self.module, "CPP_PRESET_FALLBACK_PATH", fallback_path),
            mock.patch.object(self.module, "_run", side_effect=_fake_run),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.module._build_cpp_runtime()

        error_text = str(ctx.exception)
        self.assertIn(self.module.CPP_DEPLOY_ALL_TARGET, error_text)
        self.assertIn("f8_deploy_service_runtime(...)", error_text)


class DistCiLauncherIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_dist_ci_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dist_dir = self.root / "bundle"
        self.dist_dir.mkdir(parents=True, exist_ok=True)
        launcher_output_dir = self.root / "build" / "dist"
        launcher_output_dir.mkdir(parents=True, exist_ok=True)
        (launcher_output_dir / "f8studio").write_text("launcher-binary", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_bundle_studio_launcher_runs_locally_inside_launcher_environment(self) -> None:
        with (
            mock.patch.object(self.module, "REPO_ROOT", self.root),
            mock.patch.object(self.module, "_is_running_inside_pixi_environment", return_value=True),
            mock.patch.object(self.module, "_run") as run_mock,
        ):
            self.module._bundle_studio_launcher(self.dist_dir)

        run_mock.assert_called_once_with(["python", "scripts/build_studio_launcher.py"])
        self.assertTrue((self.dist_dir / "f8studio").is_file())

    def test_bundle_studio_launcher_uses_isolated_launcher_environment(self) -> None:
        with (
            mock.patch.object(self.module, "REPO_ROOT", self.root),
            mock.patch.object(self.module, "_is_running_inside_pixi_environment", return_value=False),
            mock.patch.object(self.module, "_run") as run_mock,
        ):
            self.module._bundle_studio_launcher(self.dist_dir)

        run_mock.assert_called_once_with(["pixi", "run", "--frozen", "-e", "launcher", "build_studio_launcher"])
        self.assertTrue((self.dist_dir / "f8studio").is_file())


if __name__ == "__main__":
    unittest.main()

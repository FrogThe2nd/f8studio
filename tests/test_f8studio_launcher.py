from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_launcher_module() -> object:
    script_path = Path("scripts/f8studio_launcher.py").resolve()
    spec = importlib.util.spec_from_file_location("f8studio_launcher", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load f8studio_launcher module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LauncherEnvironmentDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_launcher_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_discover_launcher_install_environments_uses_marker_feature(self) -> None:
        (self.root / "pixi.toml").write_text(
            "[environments]\n"
            'default = { features = ["python", "studio", "launcher-runtime"] }\n'
            'onnx = { features = ["python", "onnx", "launcher-runtime"] }\n'
            'ci = { features = ["ci"] }\n',
            encoding="utf-8",
        )

        env_names = self.module._discover_launcher_install_environments(self.root)

        self.assertEqual(env_names, ["default", "onnx"])

    def test_discover_launcher_install_environments_fails_without_marker_feature(self) -> None:
        (self.root / "pixi.toml").write_text(
            "[environments]\n"
            'ci = { features = ["ci"] }\n',
            encoding="utf-8",
        )

        with self.assertRaises(ValueError) as ctx:
            self.module._discover_launcher_install_environments(self.root)

        self.assertIn("launcher-runtime", str(ctx.exception))


class LauncherInstallCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_launcher_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_install_workspace_environments_uses_targeted_environment_flags(self) -> None:
        with (
            mock.patch.object(self.module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run_mock,
            mock.patch.object(
                self.module,
                "_installed_pixi_environment_names",
                return_value={"default", "onnx"},
            ),
        ):
            ok = self.module._install_workspace_environments(
                "pixi",
                self.root,
                ["default", "onnx"],
            )

        self.assertTrue(ok)
        run_mock.assert_called_once_with(
            ["pixi", "install", "-e", "default", "-e", "onnx"],
            cwd=self.root,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()

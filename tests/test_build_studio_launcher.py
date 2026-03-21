from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_build_launcher_module() -> object:
    script_path = Path("scripts/build_studio_launcher.py").resolve()
    spec = importlib.util.spec_from_file_location("build_studio_launcher", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load build_studio_launcher module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BuildStudioLauncherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_build_launcher_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        scripts_dir = self.root / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "f8studio_launcher.py").write_text("print('launcher')\n", encoding="utf-8")
        assets_dir = self.root / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        self.icon_ico = assets_dir / "icon.ico"
        self.icon_ico.write_bytes(b"ico")
        self.splash_logo = assets_dir / "logo_with_text.png"
        self.splash_logo.write_bytes(b"png-logo")
        self.splash_icon = assets_dir / "icon.png"
        self.splash_icon.write_bytes(b"png-icon")
        self.dist_dir = self.root / "build" / "dist"
        self.dist_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_nuitka_bundles_tkinter_and_splash_assets(self) -> None:
        with (
            mock.patch.object(self.module.sys, "platform", "win32"),
            mock.patch.object(self.module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run_mock,
        ):
            exit_code = self.module._run_nuitka(
                repo_root=self.root,
                app_name="f8studio",
                icon_ico=self.icon_ico,
                splash_logo=self.splash_logo,
                splash_icon=self.splash_icon,
                dist_dir=self.dist_dir,
            )

        self.assertEqual(exit_code, 0)
        command = run_mock.call_args.args[0]
        self.assertIn("--enable-plugin=tk-inter", command)
        self.assertIn(
            f"--include-data-files={self.icon_ico}=assets/icon.ico",
            command,
        )
        self.assertIn(
            f"--include-data-files={self.splash_logo}=assets/logo_with_text.png",
            command,
        )
        self.assertIn(
            f"--include-data-files={self.splash_icon}=assets/icon.png",
            command,
        )
        self.assertIn("--windows-console-mode=disable", command)
        self.assertIn(f"--windows-icon-from-ico={self.icon_ico}", command)


if __name__ == "__main__":
    unittest.main()

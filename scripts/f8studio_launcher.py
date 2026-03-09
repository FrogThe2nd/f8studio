from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


PIXI_INSTALL_DOCS_URL = "https://pixi.prefix.dev/latest/installation/"
LAUNCHER_RUNTIME_FEATURE = "launcher-runtime"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launcher for f8pystudio via Pixi.")
    parser.add_argument("--dry-run", action="store_true", help="Print command and exit without launching.")
    return parser


def _show_error_dialog(title: str, message: str) -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()


def _launcher_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    if "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent.parent


def _find_workspace_root(start_dir: Path) -> Path:
    for candidate in (start_dir, *start_dir.parents):
        if (candidate / "pixi.toml").is_file():
            return candidate
    raise FileNotFoundError(f"pixi.toml was not found from {start_dir}")


def _pixi_candidates() -> list[Path]:
    home = Path.home()
    candidates = [home / ".pixi" / "bin" / "pixi"]
    if os.name == "nt":
        candidates.append(home / ".pixi" / "bin" / "pixi.exe")
    return candidates


def _find_pixi_executable() -> str | None:
    path_hit = shutil.which("pixi")
    if path_hit:
        return path_hit
    for candidate in _pixi_candidates():
        if candidate.is_file():
            return str(candidate)
    return None


def _installed_pixi_environment_names(workspace_root: Path) -> set[str]:
    envs_dir = workspace_root / ".pixi" / "envs"
    if not envs_dir.is_dir():
        return set()
    installed_environment_names: set[str] = set()
    for candidate in envs_dir.iterdir():
        if candidate.is_dir():
            installed_environment_names.add(candidate.name)
    return installed_environment_names


def _discover_launcher_install_environments(workspace_root: Path) -> list[str]:
    pixi_toml_path = workspace_root / "pixi.toml"
    with pixi_toml_path.open("rb") as pixi_file:
        manifest = tomllib.load(pixi_file)

    environments_table = manifest.get("environments")
    if not isinstance(environments_table, dict):
        raise ValueError("pixi.toml does not define [environments]")

    launcher_environment_names: list[str] = []
    for environment_name, environment_spec in environments_table.items():
        if not isinstance(environment_name, str):
            raise ValueError("Environment names in [environments] must be strings")
        if not isinstance(environment_spec, dict):
            continue

        features = environment_spec.get("features")
        if not isinstance(features, list):
            continue
        if LAUNCHER_RUNTIME_FEATURE in features:
            launcher_environment_names.append(environment_name)

    if not launcher_environment_names:
        raise ValueError(
            f"No launcher runtime environments found. "
            f"Add feature '{LAUNCHER_RUNTIME_FEATURE}' to at least one [environments] entry."
        )
    return launcher_environment_names


def _install_workspace_environments(
    pixi_executable: str, workspace_root: Path, environment_names: list[str]
) -> bool:
    install_command = [pixi_executable, "install"]
    for environment_name in environment_names:
        install_command.extend(["-e", environment_name])

    completed = subprocess.run(install_command, cwd=workspace_root, check=False)
    if completed.returncode != 0:
        return False
    installed_environment_names = _installed_pixi_environment_names(workspace_root)
    return all(environment_name in installed_environment_names for environment_name in environment_names)


def _install_pixi() -> bool:
    if os.name == "nt":
        install_cmd = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-c",
            "irm -useb https://pixi.sh/install.ps1 | iex",
        ]
    else:
        install_cmd = ["sh", "-c", "wget -qO- https://pixi.sh/install.sh | sh"]

    completed = subprocess.run(install_cmd, check=False)
    if completed.returncode != 0:
        return False
    return _find_pixi_executable() is not None


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    workspace_root = _find_workspace_root(_launcher_dir())
    pixi_executable = _find_pixi_executable()
    if pixi_executable is None:
        if not _install_pixi():
            _show_error_dialog(
                "f8studio launcher",
                "Pixi is required but installation failed.\n"
                f"Please install Pixi manually: {PIXI_INSTALL_DOCS_URL}",
            )
            return 2
        pixi_executable = _find_pixi_executable()
        if pixi_executable is None:
            _show_error_dialog(
                "f8studio launcher",
                "Pixi install completed but executable was not found.\n"
                f"Please install Pixi manually: {PIXI_INSTALL_DOCS_URL}",
            )
            return 2

    try:
        launcher_environment_names = _discover_launcher_install_environments(workspace_root)
    except (FileNotFoundError, tomllib.TOMLDecodeError, ValueError) as exc:
        _show_error_dialog(
            "f8studio launcher",
            f"Failed to read launcher runtime environments from pixi.toml.\n{exc}",
        )
        return 4

    installed_environment_names = _installed_pixi_environment_names(workspace_root)
    missing_environments = [
        environment_name
        for environment_name in launcher_environment_names
        if environment_name not in installed_environment_names
    ]
    install_command = [pixi_executable, "install"]
    for environment_name in missing_environments:
        install_command.extend(["-e", environment_name])
    launch_command = [pixi_executable, "run", "f8pystudio"]
    should_install_environments = len(missing_environments) > 0
    if args.dry_run:
        print("workspace:", workspace_root)
        print("environments_installed:", not should_install_environments)
        print("launcher_environments:", ", ".join(launcher_environment_names))
        if should_install_environments:
            print("missing_environments:", ", ".join(missing_environments))
        if should_install_environments:
            print("install_command:", " ".join(install_command))
        print("launch_command:", " ".join(launch_command))
        return 0

    if should_install_environments and not _install_workspace_environments(
        pixi_executable, workspace_root, missing_environments
    ):
        _show_error_dialog(
            "f8studio launcher",
            "Pixi environments are missing and installation failed.\n"
            f"Command: {' '.join(install_command)}",
        )
        return 3

    completed = subprocess.run(launch_command, cwd=workspace_root, check=False)
    if completed.returncode != 0:
        _show_error_dialog(
            "f8studio launcher",
            f"Failed to start Studio.\nCommand: {' '.join(launch_command)}\nExit code: {completed.returncode}",
        )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())

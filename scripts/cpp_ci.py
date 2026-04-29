#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE_PATH = REPO_ROOT / "conan.lock"
USER_PRESETS_PATH = REPO_ROOT / "CMakeUserPresets.json"
DEFAULT_GENERATED_PRESETS_PATH = REPO_ROOT / "build" / "Release" / "generators" / "CMakePresets.json"
GENERATED_PRESETS_PATH = DEFAULT_GENERATED_PRESETS_PATH
GENERATED_PRESET_CANDIDATES = (
    REPO_ROOT / "build" / "generators" / "CMakePresets.json",
    DEFAULT_GENERATED_PRESETS_PATH,
)
CONAN_CONFIGURE_PRESET_NAME = "conan-release"
CONAN_BUILD_PRESET_NAME = "conan-release"


@dataclass(frozen=True)
class ConanPresetSelection:
    configure_preset_name: str
    build_preset_name: str


def _run(command: list[str]) -> None:
    ccache_tmp_dir = REPO_ROOT / ".ccache-tmp"
    ccache_tmp_dir.mkdir(parents=True, exist_ok=True)

    command_env = os.environ.copy()
    command_env["CCACHE_TEMPDIR"] = str(ccache_tmp_dir)

    subprocess.run(command, check=True, cwd=REPO_ROOT, env=command_env)


def _repo_relative_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _generated_preset_path_from_user_presets() -> Path | None:
    if not USER_PRESETS_PATH.is_file():
        return None

    user_presets = json.loads(USER_PRESETS_PATH.read_text(encoding="utf-8"))
    include_entries = user_presets.get("include")
    if not isinstance(include_entries, list):
        return None

    for include_entry in include_entries:
        if not isinstance(include_entry, str):
            continue
        include_path = (REPO_ROOT / include_entry).resolve()
        if include_path.name == "CMakePresets.json" and include_path.is_file():
            return include_path
    return None


def _generated_presets_path() -> Path:
    if GENERATED_PRESETS_PATH != DEFAULT_GENERATED_PRESETS_PATH and GENERATED_PRESETS_PATH.is_file():
        return GENERATED_PRESETS_PATH

    user_preset_path = _generated_preset_path_from_user_presets()
    if user_preset_path is not None:
        return user_preset_path

    for candidate_path in GENERATED_PRESET_CANDIDATES:
        if candidate_path.is_file():
            return candidate_path

    checked_paths = ", ".join(_repo_relative_path(candidate_path) for candidate_path in GENERATED_PRESET_CANDIDATES)
    raise FileNotFoundError(f"Expected Conan-generated preset file is missing. Checked: {checked_paths}")


def _bootstrap() -> None:
    if USER_PRESETS_PATH.is_file():
        USER_PRESETS_PATH.unlink()

    if not LOCKFILE_PATH.is_file():
        raise FileNotFoundError(
            "Missing conan.lock at repository root. Run `python scripts/cpp_ci.py lock-refresh` first."
        )

    _run(["conan", "profile", "detect", "--force"])
    _run(
        [
            "conan",
            "install",
            ".",
            "-of",
            ".",
            "-s",
            "build_type=Release",
            "-s",
            "compiler.cppstd=17",
            "--build=missing",
            "--lockfile",
            "conan.lock",
            "--lockfile-partial",
        ]
    )

    _generated_presets_path()


def _load_generated_presets() -> dict[str, object]:
    generated_presets_path = _generated_presets_path()
    return json.loads(generated_presets_path.read_text(encoding="utf-8"))


def _select_conan_release_presets() -> ConanPresetSelection:
    presets = _load_generated_presets()
    configure_presets = presets.get("configurePresets", [])
    build_presets = presets.get("buildPresets", [])

    configure_preset_names = {
        preset.get("name") for preset in configure_presets if isinstance(preset, dict) and isinstance(preset.get("name"), str)
    }
    build_preset_names = {
        preset.get("name") for preset in build_presets if isinstance(preset, dict) and isinstance(preset.get("name"), str)
    }

    for build_preset in build_presets:
        if not isinstance(build_preset, dict):
            continue
        build_preset_name = build_preset.get("name")
        if build_preset_name != CONAN_BUILD_PRESET_NAME:
            continue

        configure_preset_name = build_preset.get("configurePreset")
        if not isinstance(configure_preset_name, str):
            configure_preset_name = CONAN_CONFIGURE_PRESET_NAME
        if configure_preset_name not in configure_preset_names:
            raise FileNotFoundError(
                "Generated Conan release build preset references a missing configure preset. "
                f"configurePreset={configure_preset_name!r}, configurePresets={sorted(configure_preset_names)}"
            )
        return ConanPresetSelection(
            configure_preset_name=configure_preset_name,
            build_preset_name=CONAN_BUILD_PRESET_NAME,
        )

    raise FileNotFoundError(
        "Generated Conan presets must define the canonical release build preset. "
        f"configurePresets={sorted(configure_preset_names)}, buildPresets={sorted(build_preset_names)}"
    )


def _configure() -> None:
    conan_presets = _select_conan_release_presets()
    _run(
        [
            "cmake",
            "--preset",
            conan_presets.configure_preset_name,
            "-DF8_DEPLOY_SERVICE_CLEAN=OFF",
            "-DF8_DEPLOY_SERVICE_RUNTIME_POST_BUILD=OFF",
        ]
    )


def _build() -> None:
    conan_presets = _select_conan_release_presets()
    _run(["cmake", "--build", "--preset", conan_presets.build_preset_name, "--parallel"])


def _lock_refresh() -> None:
    _run(["conan", "profile", "detect", "--force"])
    if LOCKFILE_PATH.is_file():
        LOCKFILE_PATH.unlink()
    _run(
        [
            "conan",
            "lock",
            "create",
            ".",
            "-s",
            "compiler.cppstd=17",
            "--lockfile-out",
            "conan.lock",
            "--build=missing",
        ]
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="C++ CI entrypoint for Conan + CMake.")
    parser.add_argument(
        "command",
        choices=("bootstrap", "configure", "build", "lock-refresh"),
        help="Action to run.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "bootstrap":
        _bootstrap()
    elif args.command == "configure":
        _configure()
    elif args.command == "build":
        _build()
    elif args.command == "lock-refresh":
        _lock_refresh()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

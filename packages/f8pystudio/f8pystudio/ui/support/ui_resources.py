from __future__ import annotations

from pathlib import Path


def resources_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "resources"


def icons_dir() -> Path:
    return resources_dir() / "icons"


def images_dir() -> Path:
    return resources_dir() / "images"


def licenses_dir() -> Path:
    return resources_dir() / "licenses"


def studio_logo_path() -> Path:
    return images_dir() / "logo.png"


__all__ = [
    "icons_dir",
    "images_dir",
    "licenses_dir",
    "resources_dir",
    "studio_logo_path",
]

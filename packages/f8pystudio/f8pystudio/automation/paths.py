from __future__ import annotations

from pathlib import Path


def automation_dir() -> Path:
    return Path.home() / ".f8" / "studio" / "automation"


def default_token_file() -> Path:
    return automation_dir() / "token"


def default_port_file() -> Path:
    return automation_dir() / "connection.json"

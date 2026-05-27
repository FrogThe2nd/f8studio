from __future__ import annotations

import logging
from pathlib import Path

_PATH_RESOLUTION_ERRORS = (OSError, RuntimeError, ValueError)
logger = logging.getLogger(__name__)


def package_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_weights_dir(*, extra_relative_candidates: tuple[str, ...] = ()) -> Path:
    candidates = [
        Path.cwd() / "services" / "f8" / "dl" / "weights",
        package_root() / "services" / "f8" / "dl" / "weights",
    ]
    for relative_candidate in extra_relative_candidates:
        candidate = str(relative_candidate or "").strip()
        if candidate:
            candidates.append(package_root() / candidate)

    resolved_candidates: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        resolved_candidates.append(resolved)
        try:
            if resolved.exists() and resolved.is_dir():
                return resolved
        except _PATH_RESOLUTION_ERRORS as exc:
            logger.debug("weights directory probe failed path=%s", resolved, exc_info=exc)
            continue
    return resolved_candidates[0]


def resolve_path_from_cwd_or_repo(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()

    cwd_path = (Path.cwd() / path).resolve()
    if cwd_path.exists():
        return cwd_path

    repo_path = (package_root() / path).resolve()
    if repo_path.exists():
        return repo_path
    return cwd_path

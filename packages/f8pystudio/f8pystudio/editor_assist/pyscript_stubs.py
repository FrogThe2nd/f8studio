from __future__ import annotations

from pathlib import Path, PurePosixPath


def _normalize_support_files(support_files: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_path, raw_text in support_files:
        rel_path = str(raw_path or "").strip().replace("\\", "/")
        if not rel_path:
            continue
        rel = PurePosixPath(rel_path)
        if rel.is_absolute() or ".." in rel.parts:
            continue
        normalized = str(rel)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append((normalized, str(raw_text or "")))
    return tuple(out)


def write_support_files(
    workspace_root: Path,
    *,
    support_files: tuple[tuple[str, str], ...],
    overlay_prefix: str,
) -> str:
    """
    Materialize protocol-provided typing support files and return LSP overlay text.
    """
    root = Path(workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    for rel_path, file_text in _normalize_support_files(tuple(support_files)):
        target = (root / rel_path).resolve()
        if root not in target.parents and target != root:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_text, encoding="utf-8")

    return str(overlay_prefix or "")

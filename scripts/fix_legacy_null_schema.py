#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class FixStats:
    updated_type_null: int = 0
    inferred_type_null: int = 0
    dropped_null_keys: int = 0
    files_written: int = 0


def _looks_schema_like(obj: dict[str, Any]) -> bool:
    schema_keys = {
        "type",
        "enum",
        "default",
        "properties",
        "items",
        "required",
        "oneOf",
        "anyOf",
        "allOf",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "description",
        "title",
        "$comment",
    }
    return any(key in obj for key in schema_keys)


def _normalize_null_type_value(value: Any) -> tuple[bool, str | None]:
    if value is None:
        return True, "null"
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"none", "nil", "<null>"}:
            return True, "null"
    return False, None


def _fix_tree(value: Any, stats: FixStats, *, keep_null_keys: set[str]) -> Any:
    if isinstance(value, list):
        return [_fix_tree(item, stats, keep_null_keys=keep_null_keys) for item in value]

    if not isinstance(value, dict):
        return value

    out: dict[str, Any] = {}
    for key, item in value.items():
        key_s = str(key)
        fixed_item = _fix_tree(item, stats, keep_null_keys=keep_null_keys)
        if fixed_item is None and key_s not in keep_null_keys:
            # Legacy exports often serialized optional metadata as null.
            # msgspec structs expect these keys omitted, not set to null.
            stats.dropped_null_keys += 1
            continue
        out[key_s] = fixed_item

    # Case 1: explicit broken type value in schema-like object.
    if "type" in out and _looks_schema_like(out):
        hit, normalized = _normalize_null_type_value(out.get("type"))
        if hit and normalized is not None:
            out["type"] = normalized
            stats.updated_type_null += 1

    # Case 2: missing type but enum contains null => infer type="null".
    if "type" not in out and _looks_schema_like(out):
        enum_value = out.get("enum")
        if isinstance(enum_value, list) and any(item is None for item in enum_value):
            out["type"] = "null"
            stats.inferred_type_null += 1

    return out


def _default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}.fixed{input_path.suffix}")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fix legacy JSON schema payloads where null types were serialized incorrectly."
    )
    parser.add_argument("input", type=Path, help="Input JSON file path")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output JSON file path (default: <input>.fixed.json)",
    )
    parser.add_argument(
        "--inplace",
        action="store_true",
        help="Overwrite input file in place",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check only: print stats and exit non-zero if any fixes would be applied",
    )
    parser.add_argument(
        "--keep-null-key",
        action="append",
        default=["default", "value"],
        help="Allow null for this key (repeatable). Defaults: default,value",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"input file not found: {input_path}")

    payload = _load_json(input_path)
    stats = FixStats()
    keep_null_keys = {str(k).strip() for k in list(args.keep_null_key or []) if str(k).strip()}
    fixed = _fix_tree(payload, stats, keep_null_keys=keep_null_keys)

    changed = stats.updated_type_null + stats.inferred_type_null + stats.dropped_null_keys
    if args.check:
        print(
            "check: "
            f"updates={stats.updated_type_null} "
            f"inferred={stats.inferred_type_null} "
            f"dropped_null_keys={stats.dropped_null_keys} "
            f"total={changed}"
        )
        return 1 if changed > 0 else 0

    if args.inplace:
        output_path = input_path
    else:
        output_path = Path(args.output) if args.output is not None else _default_output_path(input_path)

    _dump_json(output_path, fixed)
    stats.files_written += 1
    print(
        f"written={stats.files_written} path={output_path} "
        f"updates={stats.updated_type_null} "
        f"inferred={stats.inferred_type_null} "
        f"dropped_null_keys={stats.dropped_null_keys} "
        f"total={changed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

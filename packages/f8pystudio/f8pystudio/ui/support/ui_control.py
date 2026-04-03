from __future__ import annotations

from dataclasses import dataclass
import re


_POOL_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LANGUAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*$")
_UI_CONTROL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)(?:\[([^\[\]]*)\])?$")


@dataclass(frozen=True)
class ParsedUiControl:
    raw: str
    control_name: str
    ui_language: str
    select_pool_field: str | None
    multiselect_pool_field: str | None
    dial_loop: bool | None
    is_valid: bool


def parse_ui_control(ui_control: str) -> ParsedUiControl:
    raw = str(ui_control or "").strip()
    if not raw:
        return ParsedUiControl(
            raw="",
            control_name="",
            ui_language="",
            select_pool_field=None,
            multiselect_pool_field=None,
            dial_loop=None,
            is_valid=True,
        )

    match = _UI_CONTROL_RE.fullmatch(raw)
    if match is None:
        normalized = raw.lower()
        return ParsedUiControl(
            raw=raw,
            control_name=normalized,
            ui_language="",
            select_pool_field=None,
            multiselect_pool_field=None,
            dial_loop=None,
            is_valid=False,
        )

    control_name = str(match.group(1) or "").strip().lower()
    payload_raw = str(match.group(2) or "").strip()
    payload_lower = payload_raw.lower()

    if not payload_raw:
        return ParsedUiControl(
            raw=raw,
            control_name=control_name,
            ui_language="",
            select_pool_field=None,
            multiselect_pool_field=None,
            dial_loop=True if control_name == "dial" else None,
            is_valid=True,
        )

    if control_name in {"code", "wrapline"} and _LANGUAGE_RE.fullmatch(payload_raw):
        return ParsedUiControl(
            raw=raw,
            control_name=control_name,
            ui_language=payload_lower,
            select_pool_field=None,
            multiselect_pool_field=None,
            dial_loop=None,
            is_valid=True,
        )

    if control_name == "select" and _POOL_FIELD_RE.fullmatch(payload_raw):
        return ParsedUiControl(
            raw=raw,
            control_name=control_name,
            ui_language="",
            select_pool_field=payload_raw,
            multiselect_pool_field=None,
            dial_loop=None,
            is_valid=True,
        )

    if control_name == "multiselect" and _POOL_FIELD_RE.fullmatch(payload_raw):
        return ParsedUiControl(
            raw=raw,
            control_name=control_name,
            ui_language="",
            select_pool_field=None,
            multiselect_pool_field=payload_raw,
            dial_loop=None,
            is_valid=True,
        )

    if control_name == "dial" and payload_lower in {"loop", "noloop"}:
        return ParsedUiControl(
            raw=raw,
            control_name=control_name,
            ui_language="",
            select_pool_field=None,
            multiselect_pool_field=None,
            dial_loop=payload_lower == "loop",
            is_valid=True,
        )

    normalized = raw.lower()
    return ParsedUiControl(
        raw=raw,
        control_name=normalized,
        ui_language="",
        select_pool_field=None,
        multiselect_pool_field=None,
        dial_loop=None,
        is_valid=False,
    )


def ui_control_language(ui_control: str) -> str:
    return parse_ui_control(ui_control).ui_language

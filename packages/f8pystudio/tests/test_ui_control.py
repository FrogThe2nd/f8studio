from __future__ import annotations

from f8pystudio.ui.support.ui_control import parse_ui_control


def test_parse_ui_control_select_preserves_pool_field_case() -> None:
    parsed = parse_ui_control("select[availableToys]")

    assert parsed.control_name == "select"
    assert parsed.select_pool_field == "availableToys"
    assert parsed.multiselect_pool_field is None
    assert parsed.is_valid is True


def test_parse_ui_control_multiselect_preserves_pool_field_case() -> None:
    parsed = parse_ui_control("multiselect[modelClasses]")

    assert parsed.control_name == "multiselect"
    assert parsed.select_pool_field is None
    assert parsed.multiselect_pool_field == "modelClasses"
    assert parsed.is_valid is True


def test_parse_ui_control_normalizes_language_payload() -> None:
    parsed = parse_ui_control("code[Python]")

    assert parsed.control_name == "code"
    assert parsed.ui_language == "python"
    assert parsed.is_valid is True

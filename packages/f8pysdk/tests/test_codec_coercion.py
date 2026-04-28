from __future__ import annotations

import math

from f8pysdk.codec import (
    coerce_bool,
    coerce_flag,
    coerce_float,
    coerce_int,
    coerce_number,
    coerce_str,
    parse_bool,
    parse_float,
    parse_int,
    parse_number,
    parse_number_sequence,
    parse_str_list,
)


def test_coerce_bool_supports_default_and_empty_string_modes() -> None:
    assert coerce_bool("yes", default=False) is True
    assert coerce_bool("", default=True) is True
    assert coerce_bool("", default=True, empty_as_false=True) is False
    assert coerce_flag("", default=True) is False


def test_coerce_int_clamps_and_can_reject_bool_inputs() -> None:
    assert coerce_int("9", default=1, minimum=0, maximum=5) == 5
    assert coerce_int("bad", default=3, minimum=0, maximum=5) == 3
    assert coerce_int(True, default=7, allow_bool=False) == 7


def test_coerce_float_clamps_and_can_reject_bool_inputs() -> None:
    assert coerce_float("1.5", default=0.0, minimum=2.0, maximum=4.0) == 2.0
    assert coerce_float("bad", default=3.5) == 3.5
    assert coerce_float(True, default=2.5, allow_bool=False) == 2.5
    assert coerce_number("bad", default=1.25) == 1.25


def test_coerce_str_uses_trimmed_text_or_default() -> None:
    assert coerce_str("  hello  ", default="fallback") == "hello"
    assert coerce_str(None, default="fallback") == "fallback"
    assert coerce_str("   ", default="fallback") == "fallback"


def test_parse_helpers_return_none_for_invalid_values() -> None:
    assert parse_bool("maybe") is None
    assert parse_bool("", empty_as_false=True) is False
    assert parse_int(None) is None
    assert parse_int(True, allow_bool=False) is None
    assert parse_float("bad") is None
    assert parse_float(True) is None


def test_parse_float_can_filter_non_finite_values() -> None:
    parsed = parse_float("nan")
    assert parsed is not None
    assert math.isnan(parsed)
    assert parse_float("nan", finite_only=True) is None
    assert parse_float("inf", finite_only=True) is None
    assert parse_number("1.5") == 1.5
    assert parse_number("inf") is None


def test_parse_number_sequence_accepts_scalar_or_sequence() -> None:
    assert parse_number_sequence(1.5) == (1.5,)
    assert parse_number_sequence([1, "2.5"]) == (1.0, 2.5)
    assert parse_number_sequence(["bad"]) is None


def test_parse_str_list_supports_json_strings_and_mapping_values() -> None:
    assert parse_str_list([" a ", "", "b"]) == ["a", "b"]
    assert parse_str_list('["x", " y "]', allow_json_string=True) == ["x", "y"]
    assert parse_str_list({2: "b", 1: "a"}, allow_mapping_values=True) == ["a", "b"]
    assert parse_str_list("{bad", allow_json_string=True) is None

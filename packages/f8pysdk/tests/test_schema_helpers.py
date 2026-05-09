from __future__ import annotations

from f8pysdk.specs import array_schema, schema_default, string_schema


def test_schema_default_returns_none_for_missing_default() -> None:
    assert schema_default(string_schema()) is None


def test_array_schema_preserves_explicit_empty_default() -> None:
    schema = array_schema(items=string_schema(), default=[])

    assert schema_default(schema) == []

from __future__ import annotations

from f8pysdk.specs import array_schema, string_schema
from f8pystudio.nodegraph.state_schema import schema_enum_items


def test_schema_enum_items_reads_top_level_enum() -> None:
    schema = string_schema(enum=["Auto", "Manual"])

    assert schema_enum_items(schema) == ["Auto", "Manual"]


def test_schema_enum_items_reads_array_item_enum_from_sdk_schema() -> None:
    schema = array_schema(items=string_schema(enum=["Vagina", "Mouth"]))

    assert schema_enum_items(schema) == ["Vagina", "Mouth"]


def test_schema_enum_items_reads_array_item_enum_from_dict_schema() -> None:
    schema = {
        "type": "array",
        "items": {
            "type": "string",
            "enum": ["Vagina", "Mouth"],
        },
    }

    assert schema_enum_items(schema) == ["Vagina", "Mouth"]


def test_schema_enum_items_returns_empty_list_when_enum_is_absent() -> None:
    schema = array_schema(items=string_schema())

    assert schema_enum_items(schema) == []

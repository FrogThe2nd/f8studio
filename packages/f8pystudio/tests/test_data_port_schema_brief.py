from __future__ import annotations

import pytest

pytest.importorskip("NodeGraphQt")

from f8pysdk.specs import array_schema, complex_object_schema, number_schema, string_schema
from f8pystudio.nodegraph.service_basenode import F8StudioServiceNodeItem


def test_parse_data_port_view_name() -> None:
    assert F8StudioServiceNodeItem._parse_schema_port_view_name("[D]position") == ("data", True, "position")
    assert F8StudioServiceNodeItem._parse_schema_port_view_name("output[D]") == ("data", False, "output")
    assert F8StudioServiceNodeItem._parse_schema_port_view_name("[S]state") == ("state", True, "state")
    assert F8StudioServiceNodeItem._parse_schema_port_view_name("state[S]") == ("state", False, "state")


def test_schema_brief_from_top_level_type() -> None:
    assert F8StudioServiceNodeItem._schema_brief(number_schema()) == "number"
    assert F8StudioServiceNodeItem._schema_brief(array_schema(items=string_schema())) == "array<string>"
    assert (
        F8StudioServiceNodeItem._schema_brief(
            complex_object_schema(properties={"x": number_schema(), "y": number_schema()})
        )
        == "object[2]"
    )

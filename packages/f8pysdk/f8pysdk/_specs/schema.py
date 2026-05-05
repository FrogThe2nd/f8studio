from __future__ import annotations

from ..generated import (
    F8AnyTypeSchema,
    F8ArrayTypeSchema,
    F8BooleanTypeSchema,
    F8ComplexObjectTypeSchema,
    F8DataTypeSchema,
    F8IntegerTypeSchema,
    F8NullTypeSchema,
    F8NumberTypeSchema,
    F8StringTypeSchema,
)


def schema_type(schema: F8DataTypeSchema) -> str:
    if isinstance(schema, F8StringTypeSchema):
        return "string"
    if isinstance(schema, F8NumberTypeSchema):
        return "number"
    if isinstance(schema, F8IntegerTypeSchema):
        return "integer"
    if isinstance(schema, F8BooleanTypeSchema):
        return "boolean"
    if isinstance(schema, F8NullTypeSchema):
        return "null"
    if isinstance(schema, F8ComplexObjectTypeSchema):
        return "object"
    if isinstance(schema, F8ArrayTypeSchema):
        return "array"
    if isinstance(schema, F8AnyTypeSchema):
        return "any"
    raise TypeError(f"unsupported schema type: {type(schema).__name__}")


def schema_default(schema: F8DataTypeSchema) -> object:
    default_value = schema.default
    return None if default_value is None else default_value


def number_schema(
    *,
    default: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> F8NumberTypeSchema:
    kwargs: dict[str, object] = {}
    if default is not None:
        kwargs["default"] = default
    if minimum is not None:
        kwargs["minimum"] = minimum
    if maximum is not None:
        kwargs["maximum"] = maximum
    return F8NumberTypeSchema(**kwargs)


def string_schema(*, default: str | None = None, enum: list[str] | None = None) -> F8StringTypeSchema:
    kwargs: dict[str, object] = {}
    if default is not None:
        kwargs["default"] = default
    if enum is not None:
        kwargs["enum"] = enum
    return F8StringTypeSchema(**kwargs)


def integer_schema(
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> F8IntegerTypeSchema:
    kwargs: dict[str, object] = {}
    if default is not None:
        kwargs["default"] = default
    if minimum is not None:
        kwargs["minimum"] = minimum
    if maximum is not None:
        kwargs["maximum"] = maximum
    return F8IntegerTypeSchema(**kwargs)


def boolean_schema(*, default: bool | None = None) -> F8BooleanTypeSchema:
    kwargs: dict[str, object] = {}
    if default is not None:
        kwargs["default"] = default
    return F8BooleanTypeSchema(**kwargs)


def array_schema(
    *,
    items: F8DataTypeSchema,
) -> F8ArrayTypeSchema:
    return F8ArrayTypeSchema(items=items)


def any_schema() -> F8AnyTypeSchema:
    return F8AnyTypeSchema()


def complex_object_schema(
    *,
    properties: dict[str, F8DataTypeSchema],
) -> F8ComplexObjectTypeSchema:
    return F8ComplexObjectTypeSchema(properties=properties)


def video_frame_schema() -> F8ComplexObjectTypeSchema:
    """
    Schema marker for binary video-frame data ports.

    The frame bytes are transported by the runtime stream layer, not by JSON.
    The object schema documents the decoded envelope metadata exposed by tools.
    """

    return F8ComplexObjectTypeSchema(
        properties={
            "schemaVersion": integer_schema(default=1, minimum=1, maximum=1),
            "format": string_schema(default="bgra32", enum=["bgra32", "bgr24", "flow2_f16", "scalar1_f32"]),
            "width": integer_schema(minimum=1),
            "height": integer_schema(minimum=1),
            "pitch": integer_schema(minimum=1),
            "frameId": integer_schema(minimum=1),
            "tsMs": integer_schema(minimum=0),
        },
        field_comment="f8.payloadKind=video_frame",
    )


def audio_chunk_schema() -> F8ComplexObjectTypeSchema:
    """
    Schema marker for binary audio-chunk data ports.

    The PCM bytes are transported by the runtime stream layer, not by JSON.
    The object schema documents the decoded envelope metadata exposed by tools.
    """

    return F8ComplexObjectTypeSchema(
        properties={
            "schemaVersion": integer_schema(default=1, minimum=1, maximum=1),
            "format": string_schema(default="f32le", enum=["f32le"]),
            "sampleRate": integer_schema(minimum=1),
            "channels": integer_schema(minimum=1),
            "frames": integer_schema(minimum=1),
            "bytesPerFrame": integer_schema(minimum=1),
            "seq": integer_schema(minimum=1),
            "frameIndex": integer_schema(minimum=0),
            "tsMs": integer_schema(minimum=0),
        },
        field_comment="f8.payloadKind=audio_chunk",
    )


__all__ = [
    "audio_chunk_schema",
    "any_schema",
    "array_schema",
    "boolean_schema",
    "complex_object_schema",
    "integer_schema",
    "number_schema",
    "schema_default",
    "schema_type",
    "string_schema",
    "video_frame_schema",
]

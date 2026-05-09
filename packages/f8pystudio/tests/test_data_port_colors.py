from __future__ import annotations

from f8pysdk.specs import (
    F8DataPortPayloadKind,
    F8DataPortSpec,
    any_schema,
    audio_chunk_port,
    data_payload_spec,
    video_frame_port,
)

from f8pystudio.nodegraph.port_painter import (
    DATA_PORT_AUDIO_COLOR,
    DATA_PORT_BYTES_COLOR,
    DATA_PORT_COLOR,
    DATA_PORT_VIDEO_COLOR,
    data_port_color,
)


def test_data_port_color_maps_payload_kinds() -> None:
    json_port = F8DataPortSpec(name="json", valueSchema=any_schema())
    bytes_port = F8DataPortSpec(
        name="bytes",
        valueSchema=any_schema(),
        payload=data_payload_spec(kind=F8DataPortPayloadKind.bytes),
        payloadKind=F8DataPortPayloadKind.bytes,
    )

    assert data_port_color(json_port) == DATA_PORT_COLOR
    assert data_port_color(bytes_port) == DATA_PORT_BYTES_COLOR
    assert data_port_color(video_frame_port(name="video")) == DATA_PORT_VIDEO_COLOR
    assert data_port_color(audio_chunk_port(name="audio")) == DATA_PORT_AUDIO_COLOR

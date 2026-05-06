from __future__ import annotations

from dataclasses import dataclass

from f8pysdk.specs import F8DataPortSpec, F8OperatorSpec, any_schema, video_frame_port

from f8pystudio.nodegraph.edge_rules import validate_runtime_connection


@dataclass
class _Node:
    id: str
    svcId: str
    spec: F8OperatorSpec


def test_runtime_connection_rejects_video_frame_stream_into_text_viz_json_port() -> None:
    player = _Node(
        id="player",
        svcId="svc_player",
        spec=F8OperatorSpec(
            serviceClass="f8.implayer",
            operatorClass="f8.implayer",
            label="Player",
            dataOutPorts=[video_frame_port(name="video")],
        ),
    )
    text = _Node(
        id="text",
        svcId="studio",
        spec=F8OperatorSpec(
            serviceClass="f8.pystudio",
            operatorClass="f8.viz.text",
            label="Text Viz",
            dataInPorts=[F8DataPortSpec(name="inputData", valueSchema=any_schema())],
        ),
    )

    allowed, reason = validate_runtime_connection(
        out_port_name="video[D]",
        in_port_name="[D]inputData",
        out_node=player,
        in_node=text,
    )

    assert allowed is False
    assert "payload kind mismatch" in reason
    assert "video_frame" in reason
    assert "json" in reason


def test_runtime_connection_allows_video_frame_stream_into_video_viewer_port() -> None:
    player = _Node(
        id="player",
        svcId="svc_player",
        spec=F8OperatorSpec(
            serviceClass="f8.implayer",
            operatorClass="f8.implayer",
            label="Player",
            dataOutPorts=[video_frame_port(name="video")],
        ),
    )
    viewer = _Node(
        id="viewer",
        svcId="studio",
        spec=F8OperatorSpec(
            serviceClass="f8.pystudio",
            operatorClass="f8.viz.video",
            label="Video Viz",
            dataInPorts=[video_frame_port(name="video")],
        ),
    )

    allowed, reason = validate_runtime_connection(
        out_port_name="video[D]",
        in_port_name="[D]video",
        out_node=player,
        in_node=viewer,
    )

    assert allowed is True
    assert reason == ""

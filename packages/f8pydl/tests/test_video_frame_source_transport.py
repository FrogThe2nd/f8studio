import os
import sys


PKG_PYDL = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for path in (PKG_PYDL, PKG_SDK):
    if path not in sys.path:
        sys.path.insert(0, path)


from f8pydl.video_frame_source import video_source_metadata  # noqa: E402


def test_video_source_metadata_reports_typed_frame_stream() -> None:
    assert video_source_metadata() == {"payloadKind": "video_frame"}

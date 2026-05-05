import os
import sys


PKG_PYDL = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for path in (PKG_PYDL, PKG_SDK):
    if path not in sys.path:
        sys.path.insert(0, path)


from f8pydl.video_frame_source import select_video_source_transport  # noqa: E402


def test_select_video_source_transport_is_zenoh_first() -> None:
    assert select_video_source_transport(video_transport="", video_key="", shm_name="") == "zenoh"
    assert select_video_source_transport(video_transport="zenoh", video_key="", shm_name="shm.video") == "zenoh"
    assert select_video_source_transport(video_transport="bad", video_key="f8/svc/player/nodes/player/data/video", shm_name="") == "zenoh"


def test_select_video_source_transport_keeps_legacy_compatibility() -> None:
    assert select_video_source_transport(video_transport="", video_key="", shm_name="shm.video") == "legacy_shm"
    assert select_video_source_transport(video_transport="legacy_shm", video_key="f8/svc/player/nodes/player/data/video", shm_name="") == "legacy_shm"
    assert select_video_source_transport(video_transport="shm", video_key="", shm_name="shm.video") == "legacy_shm"

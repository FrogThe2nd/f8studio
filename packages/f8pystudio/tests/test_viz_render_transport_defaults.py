from __future__ import annotations

from f8pystudio.render_nodes.viz_audio import _normalize_audio_transport
from f8pystudio.render_nodes.viz_track import _normalize_frame_transport as normalize_track_transport
from f8pystudio.render_nodes.viz_video import _normalize_frame_transport as normalize_video_transport


def test_audio_render_transport_defaults_to_zenoh() -> None:
    assert _normalize_audio_transport("", audio_key="", shm_name="") == "zenoh"
    assert _normalize_audio_transport("invalid", audio_key="", shm_name="") == "zenoh"
    assert _normalize_audio_transport("", audio_key="f8/svc/camera/nodes/camera/data/audio", shm_name="") == "zenoh"


def test_audio_render_transport_preserves_explicit_legacy_fallback() -> None:
    assert _normalize_audio_transport("legacy_shm", audio_key="f8/test/audio", shm_name="shm.audio") == "legacy_shm"
    assert _normalize_audio_transport("", audio_key="", shm_name="shm.audio") == "legacy_shm"


def test_video_render_transport_defaults_to_zenoh() -> None:
    assert normalize_video_transport("", zenoh_key="", shm_name="") == "zenoh"
    assert normalize_video_transport("invalid", zenoh_key="", shm_name="") == "zenoh"
    assert normalize_video_transport("", zenoh_key="f8/svc/camera/nodes/camera/data/video", shm_name="") == "zenoh"


def test_video_render_transport_preserves_explicit_legacy_fallback() -> None:
    assert normalize_video_transport("legacy_shm", zenoh_key="f8/test/video", shm_name="shm.video") == "legacy_shm"
    assert normalize_video_transport("", zenoh_key="", shm_name="shm.video") == "legacy_shm"


def test_track_render_transport_matches_video_render_default_policy() -> None:
    assert normalize_track_transport("", zenoh_key="", shm_name="") == "zenoh"
    assert normalize_track_transport("", zenoh_key="f8/svc/camera/nodes/camera/data/video", shm_name="") == "zenoh"
    assert normalize_track_transport("", zenoh_key="", shm_name="shm.video") == "legacy_shm"

from __future__ import annotations

import socket
from pathlib import Path

from f8pystudio.modding import verification

FIXTURE_PATH = Path(__file__).parents[2] / "f8pysdk" / "tests" / "fixtures" / "unity_skeleton_v2.bin"


class _FakeUdpSocket:
    def __init__(self, packets: list[bytes]) -> None:
        self._packets = list(packets)
        self.closed = False

    def bind(self, _address: tuple[str, int]) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

    def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
        if self._packets:
            return self._packets.pop(0), ("127.0.0.1", 12345)
        raise socket.timeout()

    def close(self) -> None:
        self.closed = True


def test_verifier_accepts_real_binary_frame(monkeypatch) -> None:
    fake = _FakeUdpSocket([FIXTURE_PATH.read_bytes()])
    monkeypatch.setattr(verification.socket, "socket", lambda *_args: fake)

    report = verification.verify_udp_skeleton_stream(timeout_s=0.01, max_samples=1)

    assert report.listenerStatus == "verified"
    assert report.packetCount == 1
    assert report.decodedFrameCount == 1
    assert report.sampleCount == 1
    assert report.decodedSkeletonKeys == ["hs2:female:0"]
    assert report.decodedSkeletons[0]["exporterVersion"] == "0.2.0"
    assert fake.closed is True


def test_verifier_does_not_count_rejected_packet_as_sample(monkeypatch) -> None:
    fake = _FakeUdpSocket([b"not a skeleton packet"])
    monkeypatch.setattr(verification.socket, "socket", lambda *_args: fake)

    report = verification.verify_udp_skeleton_stream(timeout_s=0.01, max_samples=1)

    assert report.listenerStatus == "packets_rejected"
    assert report.packetCount == 1
    assert report.decodedFrameCount == 0
    assert report.sampleCount == 0
    assert report.decodedSkeletonKeys == []
    assert report.recentDecoderErrors

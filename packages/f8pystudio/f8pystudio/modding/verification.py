from __future__ import annotations

import json
import logging
import socket
import time
from typing import Any

from .graph_templates import skeleton_stream_graph_build_plan
from .models import DEFAULT_SKELETON_UDP_PORT, ModdingVerificationReport

logger = logging.getLogger(__name__)
_UDP_VERIFY_ERRORS = (OSError, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError)


def verify_udp_skeleton_stream(
    *,
    port: int = DEFAULT_SKELETON_UDP_PORT,
    host: str = "127.0.0.1",
    timeout_s: float = 3.0,
    max_samples: int = 8,
) -> ModdingVerificationReport:
    decoded_keys: set[str] = set()
    errors: list[str] = []
    sample_count = 0
    listener_status = "listening"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((str(host or "127.0.0.1"), int(port)))
        sock.settimeout(0.1)
        deadline = time.monotonic() + max(0.1, float(timeout_s))
        while time.monotonic() < deadline and sample_count < max(1, int(max_samples)):
            try:
                packet, _address = sock.recvfrom(1024 * 1024)
            except socket.timeout:
                continue
            sample_count += 1
            try:
                decoded_keys.update(_extract_skeleton_keys(packet))
            except _UDP_VERIFY_ERRORS as exc:
                message = f"{type(exc).__name__}: {exc}"
                if message not in errors:
                    errors.append(message)
        listener_status = "samples_received" if sample_count > 0 else "timed_out"
    except OSError as exc:
        listener_status = "bind_failed"
        errors.append(f"{type(exc).__name__}: {exc}")
        logger.info("modding UDP verification could not bind host=%s port=%s", host, port, exc_info=True)
    finally:
        sock.close()
    return ModdingVerificationReport(
        udpPort=int(port),
        listenerStatus=listener_status,
        decodedSkeletonKeys=sorted(decoded_keys),
        sampleCount=sample_count,
        recentDecoderErrors=errors,
        pyStudioEvidence={
            "listenerHost": str(host or "127.0.0.1"),
            "timeoutS": float(timeout_s),
            "maxSamples": int(max_samples),
            "verificationMode": "temporary_udp_listener",
        },
        graphBuildPlan=skeleton_stream_graph_build_plan(port=int(port)),
    )


def _extract_skeleton_keys(packet: bytes) -> list[str]:
    payload = _decode_packet_json(packet)
    if not isinstance(payload, dict):
        return []
    model_name = str(payload.get("modelName") or payload.get("model_name") or payload.get("key") or "").strip()
    if model_name:
        return [model_name]
    skeletons = payload.get("skeletons")
    if isinstance(skeletons, list):
        keys: list[str] = []
        for item in skeletons:
            if isinstance(item, dict):
                key = str(item.get("modelName") or item.get("model_name") or item.get("key") or "").strip()
                if key:
                    keys.append(key)
        return keys
    if str(payload.get("type") or "") == "skeleton_binary":
        return ["default"]
    return []


def _decode_packet_json(packet: bytes) -> Any:
    text = bytes(packet).decode("utf-8-sig")
    return json.loads(text)


__all__ = ["verify_udp_skeleton_stream"]

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Any

from f8pysdk.motion import SkeletonPacketDecodeError, decode_skeleton_datagram

from .graph_templates import skeleton_stream_graph_build_plan
from .models import DEFAULT_SKELETON_UDP_PORT, ModdingVerificationReport

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _PendingFrame:
    chunk_count: int
    chunk_indices: set[int] = field(default_factory=set)


def verify_udp_skeleton_stream(
    *,
    port: int = DEFAULT_SKELETON_UDP_PORT,
    host: str = "127.0.0.1",
    timeout_s: float = 3.0,
    max_samples: int = 8,
) -> ModdingVerificationReport:
    decoded_skeletons: dict[str, dict[str, Any]] = {}
    pending_frames: dict[tuple[str, int], _PendingFrame] = {}
    errors: list[str] = []
    packet_count = 0
    decoded_packet_count = 0
    decoded_frame_count = 0
    listener_status = "listening"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((str(host or "127.0.0.1"), int(port)))
        sock.settimeout(0.1)
        deadline = time.monotonic() + max(0.1, float(timeout_s))
        while time.monotonic() < deadline and packet_count < max(1, int(max_samples)):
            try:
                packet, _address = sock.recvfrom(1024 * 1024)
            except socket.timeout:
                continue
            packet_count += 1
            try:
                payload = decode_skeleton_datagram(packet)
                skeleton_evidence = _extract_skeleton_evidence(payload)
                if not skeleton_evidence:
                    raise SkeletonPacketDecodeError("Decoded payload does not contain a skeleton model")
                decoded_packet_count += 1
                for evidence in skeleton_evidence:
                    decoded_skeletons[str(evidence["stableKey"])] = evidence
                decoded_frame_count += _completed_frame_count(payload, pending_frames)
            except SkeletonPacketDecodeError as exc:
                message = f"{type(exc).__name__}: {exc}"
                if message not in errors:
                    errors.append(message)
                    logger.warning(
                        "modding UDP verification rejected a packet on host=%s port=%s: %s",
                        host,
                        port,
                        message,
                        exc_info=True,
                    )
        if decoded_frame_count > 0:
            listener_status = "verified"
        elif packet_count > 0:
            listener_status = "packets_rejected"
        else:
            listener_status = "timed_out"
    except OSError as exc:
        listener_status = "bind_failed"
        errors.append(f"{type(exc).__name__}: {exc}")
        logger.info("modding UDP verification could not bind host=%s port=%s", host, port, exc_info=True)
    finally:
        sock.close()
    return ModdingVerificationReport(
        udpPort=int(port),
        listenerStatus=listener_status,
        decodedSkeletonKeys=sorted(decoded_skeletons),
        decodedSkeletons=[decoded_skeletons[key] for key in sorted(decoded_skeletons)],
        packetCount=packet_count,
        decodedFrameCount=decoded_frame_count,
        sampleCount=decoded_frame_count,
        recentDecoderErrors=errors,
        pyStudioEvidence={
            "listenerHost": str(host or "127.0.0.1"),
            "timeoutS": float(timeout_s),
            "maxSamples": int(max_samples),
            "verificationMode": "temporary_udp_listener",
            "decodedPacketCount": decoded_packet_count,
            "incompleteChunkFrameCount": len(pending_frames),
        },
        graphBuildPlan=skeleton_stream_graph_build_plan(port=int(port)),
    )


def _extract_skeleton_evidence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    model_name = _model_name(payload)
    if model_name:
        return [_skeleton_evidence(payload, model_name)]
    skeletons = payload.get("skeletons")
    if isinstance(skeletons, list):
        entries: list[dict[str, Any]] = []
        for item in skeletons:
            if isinstance(item, dict):
                normalized = {str(key): value for key, value in item.items()}
                item_model_name = _model_name(normalized)
                if item_model_name:
                    entries.append(_skeleton_evidence(normalized, item_model_name))
        return entries
    return []


def _model_name(payload: dict[str, Any]) -> str:
    return str(payload.get("modelName") or payload.get("model_name") or payload.get("key") or "").strip()


def _skeleton_evidence(payload: dict[str, Any], model_name: str) -> dict[str, Any]:
    trailer_raw = payload.get("trailer")
    trailer = trailer_raw if isinstance(trailer_raw, dict) else {}
    stable_key = str(payload.get("stableKey") or trailer.get("stableKey") or model_name).strip()
    return {
        "stableKey": stable_key,
        "modelName": model_name,
        "profileId": str(trailer.get("profileId") or ""),
        "role": str(trailer.get("role") or ""),
        "roleIndex": _integer_or_default(trailer.get("roleIndex"), -1),
        "exporterVersion": str(trailer.get("exporterVersion") or ""),
        "schema": str(payload.get("schema") or ""),
    }


def _completed_frame_count(
    payload: dict[str, Any],
    pending_frames: dict[tuple[str, int], _PendingFrame],
) -> int:
    trailer_raw = payload.get("trailer")
    if not isinstance(trailer_raw, dict):
        return 1
    chunk_count = _integer_or_default(trailer_raw.get("chunkCount"), 1)
    if chunk_count <= 1:
        return 1
    chunk_index = _integer_or_default(trailer_raw.get("chunkIndex"), -1)
    frame_id = _integer_or_default(trailer_raw.get("frameId"), -1)
    stable_key = str(payload.get("stableKey") or payload.get("modelName") or "").strip()
    if not stable_key or frame_id < 0 or chunk_index < 0 or chunk_index >= chunk_count:
        return 0
    token = (stable_key, frame_id)
    pending = pending_frames.get(token)
    if pending is None or pending.chunk_count != chunk_count:
        pending = _PendingFrame(chunk_count=chunk_count)
        pending_frames[token] = pending
    pending.chunk_indices.add(chunk_index)
    if len(pending.chunk_indices) != chunk_count:
        return 0
    pending_frames.pop(token, None)
    return 1


def _integer_or_default(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


__all__ = ["verify_udp_skeleton_stream"]

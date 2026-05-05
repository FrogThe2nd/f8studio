from __future__ import annotations

from .nats_naming import ensure_token, parse_kv_key_node_state

_F8_PREFIX = "f8"


def _field_to_path(field: str) -> str:
    parts = [part for part in str(field or "").strip(".").split(".") if part]
    if not parts:
        raise ValueError("field must be non-empty")
    return "/".join(parts)


def _path_to_field(path: str) -> str:
    parts = [part for part in str(path or "").strip("/").split("/") if part]
    if not parts:
        raise ValueError("field path must be non-empty")
    return ".".join(parts)


def zenoh_data_key(service_id: str, *, node_id: str, port_id: str) -> str:
    sid = ensure_token(service_id, label="service_id")
    nid = ensure_token(node_id, label="node_id")
    pid = ensure_token(port_id, label="port_id")
    return f"{_F8_PREFIX}/svc/{sid}/nodes/{nid}/data/{pid}"


def zenoh_endpoint_key(service_id: str, endpoint: str) -> str:
    sid = ensure_token(service_id, label="service_id")
    ep = ensure_token(endpoint, label="endpoint")
    return f"{_F8_PREFIX}/svc/{sid}/endpoint/{ep}"


def zenoh_cmd_key(service_id: str) -> str:
    sid = ensure_token(service_id, label="service_id")
    return f"{_F8_PREFIX}/svc/{sid}/cmd"


def zenoh_service_liveliness_key(service_id: str) -> str:
    sid = ensure_token(service_id, label="service_id")
    return f"{_F8_PREFIX}/live/svc/{sid}"


def zenoh_studio_liveliness_key(studio_service_id: str) -> str:
    sid = ensure_token(studio_service_id, label="studio_service_id")
    return f"{_F8_PREFIX}/live/studio/{sid}"


def zenoh_state_key(service_id: str, *, node_id: str, field: str) -> str:
    sid = ensure_token(service_id, label="service_id")
    nid = ensure_token(node_id, label="node_id")
    field_path = _field_to_path(field)
    return f"{_F8_PREFIX}/svc/{sid}/state/nodes/{nid}/state/{field_path}"


def zenoh_kv_key(service_id: str, key: str) -> str:
    parsed = parse_kv_key_node_state(key)
    if parsed is not None:
        node_id, field = parsed
        return zenoh_state_key(service_id, node_id=node_id, field=field)
    sid = ensure_token(service_id, label="service_id")
    key_path = "/".join(part for part in str(key or "").strip(".").split(".") if part)
    if not key_path:
        raise ValueError("key must be non-empty")
    return f"{_F8_PREFIX}/svc/{sid}/kv/{key_path}"


def zenoh_kv_pattern(service_id: str, key_pattern: str) -> str:
    pattern = str(key_pattern or "").strip()
    if not pattern:
        raise ValueError("key_pattern must be non-empty")
    if pattern.endswith(">"):
        prefix = pattern[:-1].rstrip(".")
        parsed_prefix = [part for part in prefix.split(".") if part]
        if parsed_prefix and parsed_prefix[0] == "nodes":
            sid = ensure_token(service_id, label="service_id")
            if len(parsed_prefix) == 1:
                return f"{_F8_PREFIX}/svc/{sid}/state/nodes/**"
            node_id = ensure_token(parsed_prefix[1], label="node_id")
            if len(parsed_prefix) == 2:
                return f"{_F8_PREFIX}/svc/{sid}/state/nodes/{node_id}/**"
            if len(parsed_prefix) >= 3 and parsed_prefix[2] == "state":
                return f"{_F8_PREFIX}/svc/{sid}/state/nodes/{node_id}/state/**"
        prefix_path = "/".join(part for part in prefix.split(".") if part)
        sid = ensure_token(service_id, label="service_id")
        return f"{_F8_PREFIX}/svc/{sid}/kv/{prefix_path}/**"
    return zenoh_kv_key(service_id, pattern)


def kv_bucket_to_service_id(bucket: str) -> str:
    text = str(bucket or "").strip()
    prefix = "svc_"
    if not text.startswith(prefix):
        raise ValueError(f"unsupported service KV bucket: {bucket!r}")
    return ensure_token(text[len(prefix) :], label="service_id")


def zenoh_key_to_kv_key(key: str) -> str | None:
    text = str(key or "").strip("/")
    parts = text.split("/")
    if len(parts) >= 8 and parts[0] == _F8_PREFIX and parts[1] == "svc" and parts[3] == "state":
        if parts[4] != "nodes" or parts[6] != "state":
            return None
        node_id = parts[5]
        field = _path_to_field("/".join(parts[7:]))
        return f"nodes.{node_id}.state.{field}"
    if len(parts) >= 5 and parts[0] == _F8_PREFIX and parts[1] == "svc" and parts[3] == "kv":
        return ".".join(parts[4:])
    return None


def subject_to_zenoh_key(subject: str) -> str:
    text = str(subject or "").strip(".")
    parts = text.split(".")
    if len(parts) == 6 and parts[0] == "svc" and parts[2] == "nodes" and parts[4] == "data":
        return zenoh_data_key(parts[1], node_id=parts[3], port_id=parts[5])
    if len(parts) == 3 and parts[0] == "svc" and parts[2] == "cmd":
        return zenoh_cmd_key(parts[1])
    if len(parts) == 3 and parts[0] == "svc":
        return zenoh_endpoint_key(parts[1], parts[2])
    if "*" in text:
        return text.replace(".", "/").replace("*", "*")
    return text.replace(".", "/")


def zenoh_key_to_subject(key: str) -> str:
    text = str(key or "").strip("/")
    parts = text.split("/")
    if len(parts) == 7 and parts[0] == _F8_PREFIX and parts[1] == "svc" and parts[3] == "nodes":
        if parts[5] == "data":
            return f"svc.{parts[2]}.nodes.{parts[4]}.data.{parts[6]}"
    if len(parts) == 4 and parts[0] == _F8_PREFIX and parts[1] == "svc" and parts[3] == "cmd":
        return f"svc.{parts[2]}.cmd"
    if len(parts) == 5 and parts[0] == _F8_PREFIX and parts[1] == "svc" and parts[3] == "endpoint":
        return f"svc.{parts[2]}.{parts[4]}"
    return text.replace("/", ".")


__all__ = [
    "kv_bucket_to_service_id",
    "subject_to_zenoh_key",
    "zenoh_cmd_key",
    "zenoh_data_key",
    "zenoh_endpoint_key",
    "zenoh_key_to_kv_key",
    "zenoh_key_to_subject",
    "zenoh_kv_key",
    "zenoh_kv_pattern",
    "zenoh_service_liveliness_key",
    "zenoh_state_key",
    "zenoh_studio_liveliness_key",
]

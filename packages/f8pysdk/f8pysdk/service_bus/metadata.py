from __future__ import annotations

from typing import Any

from .state_write import StateWriteSource


_STATE_PAYLOAD_RESERVED_KEYS = frozenset(("value", "actor", "ts", "source", "origin"))
_HIDDEN_COMMAND_FORWARD_EXCLUDED_KEYS = frozenset(("value", "actor", "ts", "origin"))


def _resolved_source_text(source: StateWriteSource | str | None, *, default: str) -> str:
    if isinstance(source, StateWriteSource):
        return source.value
    text = str(source or "").strip()
    return text or str(default)


def build_hidden_command_call_meta(*, command_input_field: str, meta: dict[str, Any]) -> dict[str, Any]:
    """
    Metadata forwarded to `node.on_command(...)` for hidden-state command inputs.
    """
    call_meta: dict[str, Any] = {}
    for key, value in dict(meta).items():
        if key in _HIDDEN_COMMAND_FORWARD_EXCLUDED_KEYS:
            continue
        call_meta[str(key)] = value
    call_meta.setdefault("source", StateWriteSource.state_edge_intra.value)
    call_meta.setdefault("commandInputField", str(command_input_field))
    return call_meta


def build_command_output_meta(*, command_name: str, command_input_field: str) -> dict[str, Any]:
    """
    Metadata persisted on hidden command output state writeback.
    """
    return {
        "command": str(command_name),
        "commandInputField": str(command_input_field),
        "source": StateWriteSource.cmd.value,
    }


def build_intra_state_route_meta(*, from_node_id: str, from_field: str) -> dict[str, Any]:
    """
    Metadata for intra-service state edge fanout writes.
    """
    return {
        "fromNodeId": str(from_node_id),
        "fromField": str(from_field),
    }


def build_cross_state_meta(
    *, peer_service_id: str, remote_key: str, inbound_meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Metadata for cross-service state binding application.
    """
    meta_out: dict[str, Any] = {
        "peerServiceId": str(peer_service_id),
        "remoteKey": str(remote_key),
    }
    for key, value in dict(inbound_meta or {}).items():
        if key in _STATE_PAYLOAD_RESERVED_KEYS:
            continue
        meta_out[str(key)] = value
    return meta_out


def build_state_validation_meta(
    *, source: StateWriteSource | str | None, meta: dict[str, Any] | None
) -> dict[str, Any]:
    """
    Metadata passed into `validate_state(...)` implementations.

    This keeps source tagging explicit while preserving caller metadata.
    """
    out = dict(meta or {})
    out.setdefault("source", _resolved_source_text(source, default=""))
    if not out.get("source"):
        out.pop("source", None)
    return out


def build_rungraph_reconcile_meta() -> dict[str, Any]:
    """
    Metadata for rungraph-driven `stateValues` reconciliation writes.
    """
    return {
        "via": "rungraph",
        "rungraphReconcile": True,
    }


def build_builtin_identity_state_meta() -> dict[str, Any]:
    """
    Metadata for SDK-owned builtin identity state seeding.
    """
    return {"builtin": True}


def build_lifecycle_event_meta(
    *, source: StateWriteSource | str | None, meta: dict[str, Any] | None
) -> dict[str, Any]:
    """
    Metadata delivered to lifecycle callbacks and service hooks.

    This preserves the current compatibility behavior where caller-provided
    metadata may override the default `source` field.
    """
    return {
        "source": _resolved_source_text(source, default=StateWriteSource.runtime.value),
        **(dict(meta or {})),
    }


def build_lifecycle_state_meta(*, meta: dict[str, Any] | None) -> dict[str, Any]:
    """
    Metadata persisted when the SDK writes builtin `active` lifecycle state.
    """
    return {
        "lifecycle": True,
        **(dict(meta or {})),
    }

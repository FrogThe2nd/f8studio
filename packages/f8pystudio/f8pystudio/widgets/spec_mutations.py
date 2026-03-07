from __future__ import annotations

from f8pysdk.msgspec_codec import copy_model
from typing import Any, Callable, Iterable

from f8pysdk import F8Command, F8DataPortSpec, F8OperatorSpec, F8ServiceSpec, F8StateSpec


def _mutate_or_copy(model: Any, *, mutate: Callable[[Any], None], update: dict[str, Any] | None = None) -> Any:
    """
    Apply mutation to a msgspec struct instance.

    Some specs in the project appear mutable, others effectively behave as immutable.
    This helper writes in-place when possible, otherwise deep-copies and returns the copy.
    """
    try:
        mutate(model)
        return model
    except (AttributeError, RuntimeError, TypeError, ValueError):
        try:
            if update is not None:
                return copy_model(model, deep=True, update=update)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        m2 = copy_model(model, deep=True)
        mutate(m2)
        return m2


def replace_state_field(spec: F8ServiceSpec | F8OperatorSpec, *, old_name: str, new_field: F8StateSpec) -> Any:
    old = str(old_name or "").strip()
    fields = list(spec.stateFields or [])
    out: list[F8StateSpec] = []
    replaced = False
    for f in fields:
        if str(f.name or "").strip() == old:
            out.append(new_field)
            replaced = True
        else:
            out.append(f)
    if not replaced:
        out.append(new_field)

    def _mutate(s: Any) -> None:
        s.stateFields = out

    return _mutate_or_copy(spec, mutate=_mutate, update={"stateFields": out})


def add_state_field(spec: F8ServiceSpec | F8OperatorSpec, *, field: F8StateSpec) -> Any:
    fields = list(spec.stateFields or [])
    fields.append(field)

    def _mutate(s: Any) -> None:
        s.stateFields = fields

    return _mutate_or_copy(spec, mutate=_mutate, update={"stateFields": fields})


def delete_state_field(spec: F8ServiceSpec | F8OperatorSpec, *, name: str) -> Any:
    n = str(name or "").strip()
    fields = [f for f in list(spec.stateFields or []) if str(f.name or "").strip() != n]

    def _mutate(s: Any) -> None:
        s.stateFields = fields

    return _mutate_or_copy(spec, mutate=_mutate, update={"stateFields": fields})


def add_command(spec: F8ServiceSpec, *, cmd: F8Command) -> F8ServiceSpec:
    cmds = list(spec.commands or [])
    cmds.append(cmd)

    def _mutate(s: Any) -> None:
        s.commands = cmds

    return _mutate_or_copy(spec, mutate=_mutate, update={"commands": cmds})


def replace_command(spec: F8ServiceSpec, *, name: str, cmd: F8Command) -> F8ServiceSpec:
    n = str(name or "").strip()
    cmds = list(spec.commands or [])
    out: list[F8Command] = []
    replaced = False
    for c in cmds:
        if str(c.name or "").strip() == n:
            out.append(cmd)
            replaced = True
        else:
            out.append(c)
    if not replaced:
        out.append(cmd)

    def _mutate(s: Any) -> None:
        s.commands = out

    return _mutate_or_copy(spec, mutate=_mutate, update={"commands": out})


def delete_command(spec: F8ServiceSpec, *, name: str) -> F8ServiceSpec:
    n = str(name or "").strip()
    for c in list(spec.commands or []):
        if str(c.name or "").strip() == n and bool(c.required):
            return spec
    cmds = [c for c in list(spec.commands or []) if str(c.name or "").strip() != n]

    def _mutate(s: Any) -> None:
        s.commands = cmds

    return _mutate_or_copy(spec, mutate=_mutate, update={"commands": cmds})


def set_ports(
    spec: F8ServiceSpec | F8OperatorSpec,
    *,
    data_in: Iterable[F8DataPortSpec],
    data_out: Iterable[F8DataPortSpec],
    exec_in: Iterable[str] | None = None,
    exec_out: Iterable[str] | None = None,
) -> Any:
    data_in_l = list(data_in)
    data_out_l = list(data_out)
    exec_in_l = list(exec_in or [])
    exec_out_l = list(exec_out or [])

    def _port_name(port: F8DataPortSpec) -> str:
        return str(port.name or "").strip()

    def _merge_with_required(
        *,
        existing: list[F8DataPortSpec],
        requested: list[F8DataPortSpec],
    ) -> list[F8DataPortSpec]:
        # Keep required ports stable at the front (existing order), while preserving
        # requested order for the editable portion (rename/reorder UX).
        requested_by_name: dict[str, F8DataPortSpec] = {}
        requested_order: list[str] = []
        for port in requested:
            name = _port_name(port)
            if not name:
                continue
            if name not in requested_by_name:
                requested_order.append(name)
            requested_by_name[name] = port

        out: list[F8DataPortSpec] = []
        seen: set[str] = set()
        existing_required_names: set[str] = set()

        for port in existing:
            name = _port_name(port)
            if not name:
                continue
            if not bool(port.required):
                continue
            existing_required_names.add(name)
            chosen = requested_by_name.get(name, port)
            out.append(chosen)
            seen.add(name)

        for name in requested_order:
            requested_port = requested_by_name.get(name)
            if requested_port is None:
                continue
            if name in existing_required_names:
                continue
            out.append(requested_port)
            seen.add(name)

        return out

    existing_data_in = list(spec.dataInPorts or [])
    existing_data_out = list(spec.dataOutPorts or [])
    data_in_l = _merge_with_required(existing=existing_data_in, requested=data_in_l)
    data_out_l = _merge_with_required(existing=existing_data_out, requested=data_out_l)

    def _mutate(s: Any) -> None:
        s.dataInPorts = data_in_l
        s.dataOutPorts = data_out_l
        if isinstance(s, F8OperatorSpec):
            s.execInPorts = exec_in_l
            s.execOutPorts = exec_out_l

    update: dict[str, Any] = {"dataInPorts": data_in_l, "dataOutPorts": data_out_l}
    if isinstance(spec, F8OperatorSpec):
        update["execInPorts"] = exec_in_l
        update["execOutPorts"] = exec_out_l

    return _mutate_or_copy(spec, mutate=_mutate, update=update)


def is_service_spec(spec: Any) -> bool:
    return isinstance(spec, F8ServiceSpec)


def is_operator_spec(spec: Any) -> bool:
    return isinstance(spec, F8OperatorSpec)

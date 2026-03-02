from __future__ import annotations

from pathlib import Path


def _normalize_names(values: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return tuple(out)


def _typed_dict_lines(type_name: str, names: tuple[str, ...]) -> list[str]:
    if not names:
        return [f"{type_name} = TypedDict('{type_name}', {{}}, total=False)"]
    lines = [f"{type_name} = TypedDict(", f"    '{type_name}',", "    {"]
    for name in names:
        lines.append(f"        {name!r}: Any,")
    lines.extend(["    },", "    total=False,", ")"])
    return lines


def _build_stub_text(*, data_in_ports: tuple[str, ...], data_out_ports: tuple[str, ...], state_fields: tuple[str, ...]) -> str:
    in_names = _normalize_names(data_in_ports)
    out_names = _normalize_names(data_out_ports)
    state_names = _normalize_names(state_fields)

    lines: list[str] = [
        "from __future__ import annotations",
        "",
        "from typing import Any, Awaitable, Callable, Protocol, TypedDict",
        "",
    ]
    lines.extend(_typed_dict_lines("F8Inputs", in_names))
    lines.append("")
    lines.extend(_typed_dict_lines("F8Outputs", out_names))
    lines.append("")
    lines.extend(_typed_dict_lines("F8StateFields", state_names))
    lines.extend(
        [
            "",
            "class F8Permission(TypedDict):",
            "    localExecGranted: bool",
            "    expiresTsMs: int | None",
            "    grantTsMs: int",
            "    sessionId: str",
            "",
            "class F8Tick(TypedDict):",
            "    seq: int",
            "    tsMs: int",
            "    deltaMs: int",
            "",
            "class F8PyScriptContext(TypedDict):",
            "    serviceId: str",
            "    locals: dict[str, Any]",
            "    permission: F8Permission",
            "    log: Callable[[object], None]",
            "    emit: Callable[[str, Any], None]",
            "    emit_async: Callable[[str, Any], Awaitable[None]]",
            "    set_state: Callable[[str, Any], None]",
            "    set_state_async: Callable[[str, Any], Awaitable[None]]",
            "    get_state: Callable[[str], Awaitable[Any]]",
            "    get_state_cached: Callable[[str, Any], Any]",
            "    subscribe_video_shm: Callable[[str, str], None]",
            "    get_video_shm: Callable[[str], dict[str, Any] | None]",
            "    unsubscribe_video_shm: Callable[[str], None]",
            "    list_video_shm_subscriptions: Callable[[], list[dict[str, Any]]]",
            "    exec_local: Callable[..., Awaitable[dict[str, Any]]]",
            "",
            "class F8PyEngineContext(TypedDict):",
            "    nodeId: str",
            "    locals: dict[str, Any]",
            "    execIn: str | None",
            "    log: Callable[[str], None]",
            "    emit: Callable[[str, Any], None]",
            "    emit_async: Callable[[str, Any], Awaitable[None]]",
            "    set_state: Callable[[str, Any], None]",
            "    set_state_async: Callable[[str, Any], Awaitable[None]]",
            "    get_state: Callable[[str], Awaitable[Any]]",
            "    get_state_cached: Callable[[str, Any], Any]",
            "    subscribe_video_shm: Callable[[str, str], None]",
            "    get_video_shm: Callable[[str], dict[str, Any] | None]",
            "    unsubscribe_video_shm: Callable[[str], None]",
            "    list_video_shm_subscriptions: Callable[[], list[dict[str, Any]]]",
            "",
            "class PyScriptOnStartHook(Protocol):",
            "    def __call__(self, ctx: F8PyScriptContext) -> Any: ...",
            "",
            "class PyScriptOnStopHook(Protocol):",
            "    def __call__(self, ctx: F8PyScriptContext) -> Any: ...",
            "",
            "class PyScriptOnPauseHook(Protocol):",
            "    def __call__(self, ctx: F8PyScriptContext, meta: dict[str, Any] | None = None) -> Any: ...",
            "",
            "class PyScriptOnResumeHook(Protocol):",
            "    def __call__(self, ctx: F8PyScriptContext, meta: dict[str, Any] | None = None) -> Any: ...",
            "",
            "class PyScriptOnStateHook(Protocol):",
            "    def __call__(self, ctx: F8PyScriptContext, field: str, value: Any, tsMs: int | None = None) -> Any: ...",
            "",
            "class PyScriptOnDataHook(Protocol):",
            "    def __call__(self, ctx: F8PyScriptContext, port: str, value: Any, tsMs: int | None = None) -> Any: ...",
            "",
            "class PyScriptOnTickHook(Protocol):",
            "    def __call__(self, ctx: F8PyScriptContext, tick: F8Tick) -> Any: ...",
            "",
            "class PyScriptOnCommandHook(Protocol):",
            "    def __call__(self, ctx: F8PyScriptContext, name: str, args: dict[str, Any], meta: dict[str, Any] | None = None) -> Any: ...",
            "",
            "class PyEngineOnStartHook(Protocol):",
            "    def __call__(self, ctx: F8PyEngineContext) -> Any: ...",
            "",
            "class PyEngineOnStateHook(Protocol):",
            "    def __call__(self, ctx: F8PyEngineContext, field: str, value: Any, tsMs: int | None = None) -> Any: ...",
            "",
            "class PyEngineOnMsgHook(Protocol):",
            "    def __call__(self, ctx: F8PyEngineContext, inputs: F8Inputs) -> Any: ...",
            "",
            "class PyEngineOnExecHook(Protocol):",
            "    def __call__(self, ctx: F8PyEngineContext, execIn: str, inputs: F8Inputs) -> Any: ...",
            "",
            "class PyEngineOnStopHook(Protocol):",
            "    def __call__(self, ctx: F8PyEngineContext) -> Any: ...",
            "",
        ]
    )
    return "\n".join(lines)


def _build_overlay_prefix(*, mode: str) -> str:
    if mode == "f8.pyscript_service":
        lines = [
            "from __future__ import annotations",
            "from f8_script_api import (",
            "    PyScriptOnCommandHook as _F8OnCommandHook,",
            "    PyScriptOnDataHook as _F8OnDataHook,",
            "    PyScriptOnPauseHook as _F8OnPauseHook,",
            "    PyScriptOnResumeHook as _F8OnResumeHook,",
            "    PyScriptOnStartHook as _F8OnStartHook,",
            "    PyScriptOnStateHook as _F8OnStateHook,",
            "    PyScriptOnStopHook as _F8OnStopHook,",
            "    PyScriptOnTickHook as _F8OnTickHook,",
            ")",
            "onStart: _F8OnStartHook",
            "onStop: _F8OnStopHook",
            "onPause: _F8OnPauseHook",
            "onResume: _F8OnResumeHook",
            "onState: _F8OnStateHook",
            "onData: _F8OnDataHook",
            "onTick: _F8OnTickHook",
            "onCommand: _F8OnCommandHook",
            "",
        ]
        return "\n".join(lines)

    if mode == "f8.pyengine_operator":
        lines = [
            "from __future__ import annotations",
            "from f8_script_api import (",
            "    PyEngineOnExecHook as _F8OnExecHook,",
            "    PyEngineOnMsgHook as _F8OnMsgHook,",
            "    PyEngineOnStartHook as _F8OnStartHook,",
            "    PyEngineOnStateHook as _F8OnStateHook,",
            "    PyEngineOnStopHook as _F8OnStopHook,",
            ")",
            "onStart: _F8OnStartHook",
            "onState: _F8OnStateHook",
            "onMsg: _F8OnMsgHook",
            "onExec: _F8OnExecHook",
            "onStop: _F8OnStopHook",
            "",
        ]
        return "\n".join(lines)

    return ""


def write_support_files(
    workspace_root: Path,
    *,
    mode: str,
    data_in_ports: tuple[str, ...],
    data_out_ports: tuple[str, ...],
    state_fields: tuple[str, ...],
) -> str:
    """
    Materialize typing support files and return LSP-only overlay prefix text.
    """
    root = Path(workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    stub_text = _build_stub_text(
        data_in_ports=data_in_ports,
        data_out_ports=data_out_ports,
        state_fields=state_fields,
    )
    (root / "f8_script_api.pyi").write_text(stub_text, encoding="utf-8")

    return _build_overlay_prefix(mode=mode)

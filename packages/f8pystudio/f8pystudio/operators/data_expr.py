from __future__ import annotations

import logging
import time
from types import CodeType
from typing import Any

from f8pysdk import (
    F8DataPortSpec,
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8SpecEditPolicy,
    F8StateAccess,
    F8StateSpec,
    any_schema,
    boolean_schema,
    editable_collection_edit_policy,
    string_schema,
)
from f8pysdk.nats_naming import ensure_token
from f8pysdk.runtime_node import OperatorNode
from f8pysdk.runtime_node_registry import RuntimeNodeRegistry

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS
from ._py_expr_eval import (
    coerce_bool as _coerce_bool,
    compile_expr as _compile_expr,
    is_identifier as _is_identifier,
    normalize_expr_code,
    safe_eval_compiled as _safe_eval_compiled,
    unwrap_wrapped_value,
    wrap_value as _wrap_value,
)

OPERATOR_CLASS = "f8.data_expr"

logger = logging.getLogger(__name__)


class DataExprRuntimeNode(OperatorNode):
    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        data_in_ports = [p.name for p in (node.dataInPorts or [])] or ["input"]
        data_out_ports = [p.name for p in (node.dataOutPorts or [])] or ["out"]
        state_fields = [s.name for s in (node.stateFields or [])] or ["code"]
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=data_in_ports,
            data_out_ports=data_out_ports,
            state_fields=state_fields,
        )
        self._initial_state = dict(initial_state or {})
        self._code = self._normalize_code(self._initial_state.get("code") or "input")
        self._allow_numpy = _coerce_bool(self._initial_state.get("allowNumpy"), default=False)
        self._unpack_dict_outputs = _coerce_bool(self._initial_state.get("unpackDictOutputs"), default=False)
        self._compiled: CodeType | None = None
        self._compile_error: str | None = None
        self._recompile()

        self._last_ctx_id: str | int | None = None
        self._last_outputs: dict[str, Any] = {}
        self._dirty = True
        self._last_eval_exc_sig = ""
        self._last_eval_exc_log_ts_ms = 0
        self._last_pull_exc_sig = ""
        self._last_pull_exc_log_ts_ms = 0
        self._last_unmatched_exc_sig = ""
        self._last_unmatched_exc_log_ts_ms = 0

    def _should_log_repeating_error(self, sig: str, *, now_ms: int, kind: str) -> bool:
        if kind == "eval":
            if sig != self._last_eval_exc_sig:
                self._last_eval_exc_sig = sig
                self._last_eval_exc_log_ts_ms = int(now_ms)
                return True
            if (int(now_ms) - int(self._last_eval_exc_log_ts_ms)) >= 5000:
                self._last_eval_exc_log_ts_ms = int(now_ms)
                return True
            return False
        if kind == "pull":
            if sig != self._last_pull_exc_sig:
                self._last_pull_exc_sig = sig
                self._last_pull_exc_log_ts_ms = int(now_ms)
                return True
            if (int(now_ms) - int(self._last_pull_exc_log_ts_ms)) >= 5000:
                self._last_pull_exc_log_ts_ms = int(now_ms)
                return True
            return False
        if kind == "unmatched":
            if sig != self._last_unmatched_exc_sig:
                self._last_unmatched_exc_sig = sig
                self._last_unmatched_exc_log_ts_ms = int(now_ms)
                return True
            if (int(now_ms) - int(self._last_unmatched_exc_log_ts_ms)) >= 5000:
                self._last_unmatched_exc_log_ts_ms = int(now_ms)
                return True
            return False
        return True

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        _ = ts_ms
        name = str(field or "")
        if name == "allowNumpy":
            self._allow_numpy = _coerce_bool(value, default=False)
            self._recompile()
            self._dirty = True
            return
        if name == "unpackDictOutputs":
            self._unpack_dict_outputs = _coerce_bool(value, default=False)
            self._dirty = True
            return
        if name != "code":
            return
        self._code = self._normalize_code(value)
        self._recompile()
        self._dirty = True

    async def validate_state(self, field: str, value: Any, *, ts_ms: int, meta: dict[str, Any]) -> Any:
        del ts_ms, meta
        name = str(field or "").strip()
        if name == "allowNumpy":
            return _coerce_bool(value, default=False)
        if name == "unpackDictOutputs":
            return _coerce_bool(value, default=False)
        if name == "code":
            return self._normalize_code(value)
        return value

    @staticmethod
    def _normalize_code(value: Any) -> str:
        return normalize_expr_code(value)

    def _recompile(self) -> None:
        compiled, err = _compile_expr(self._code, allow_numpy=self._allow_numpy)
        self._compiled = compiled
        self._compile_error = err

    def _build_eval_names(self, inputs: dict[str, Any]) -> dict[str, Any]:
        env: dict[str, Any] = {}
        env["inputs"] = {key: _wrap_value(value) for key, value in inputs.items()}
        for key, value in inputs.items():
            if _is_identifier(key):
                env[key] = _wrap_value(value)
        return env

    def _default_output_port(self) -> str | None:
        if "out" in self.data_out_ports:
            return "out"
        if self.data_out_ports:
            return str(self.data_out_ports[0])
        return None

    def _normalize_output_value(self, value: Any) -> Any:
        return unwrap_wrapped_value(value)

    def _extract_outputs(self, result: Any) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        if isinstance(result, dict) and self._unpack_dict_outputs:
            for key, value in result.items():
                key_s = str(key)
                if key_s in self.data_out_ports:
                    outputs[key_s] = self._normalize_output_value(value)
                    continue
                now_ms = int(time.time() * 1000.0)
                sig = f"unmatched_port:{key_s}"
                if self._should_log_repeating_error(sig, now_ms=now_ms, kind="unmatched"):
                    logger.warning("[%s:data_expr] unpack key has no matching output port: %s", self.node_id, key_s)
            return outputs
        default_port = self._default_output_port()
        if default_port is None:
            return outputs
        outputs[default_port] = self._normalize_output_value(result)
        return outputs

    async def compute_output(self, port: str, ctx_id: str | int | None = None) -> Any:
        out_port = str(port or "")
        if out_port not in self.data_out_ports:
            return None
        if not self._dirty and ctx_id is not None and ctx_id == self._last_ctx_id:
            return self._last_outputs.get(out_port)

        pulled: dict[str, Any] = {}
        for port_name in list(self.data_in_ports or []):
            try:
                pulled[str(port_name)] = await self.pull(str(port_name), ctx_id=ctx_id)
            except Exception as exc:
                pulled[str(port_name)] = None
                now_ms = int(time.time() * 1000.0)
                sig = f"{type(exc).__name__}:{exc}:port={port_name}"
                if self._should_log_repeating_error(sig, now_ms=now_ms, kind="pull"):
                    logger.exception("[%s:data_expr] pull failed (port=%s)", self.node_id, port_name)

        try:
            if self._compiled is None:
                raise ValueError(self._compile_error or "invalid expression")
            out = _safe_eval_compiled(
                self._compiled,
                names=self._build_eval_names(pulled),
                allow_numpy=self._allow_numpy,
            )
        except Exception as exc:
            now_ms = int(time.time() * 1000.0)
            sig = f"{type(exc).__name__}:{exc}"
            if self._should_log_repeating_error(sig, now_ms=now_ms, kind="eval"):
                logger.warning("[%s:data_expr] eval failed: %s", self.node_id, exc)
            out = None

        self._last_outputs = self._extract_outputs(out)
        self._last_ctx_id = ctx_id
        self._dirty = False
        return self._last_outputs.get(out_port)


DataExprRuntimeNode.SPEC = F8OperatorSpec(
    schemaVersion=F8OperatorSchemaVersion.f8operator_1,
    serviceClass=SERVICE_CLASS,
    paletteCategory=SERVICE_CLASS,
    operatorClass=OPERATOR_CLASS,
    version="0.0.1",
    label="Studio Data Expr",
    description=(
        "Studio-local data expression node for quick value transforms inside the embedded PyStudio runtime. "
        "Best for lightweight glue between local controls, viz nodes, and other embedded studio operators."
    ),
    tags=["studio", "local", "expr", "math", "logic", "transform"],
    dataInPorts=[
        F8DataPortSpec(name="x", description="Input value for the expression.", valueSchema=any_schema(), required=False),
    ],
    dataOutPorts=[
        F8DataPortSpec(name="out", description="Expression result.", valueSchema=any_schema(), required=False),
    ],
    editPolicy=F8SpecEditPolicy(
        dataInPorts=editable_collection_edit_policy(),
        dataOutPorts=editable_collection_edit_policy(),
    ),
    stateFields=[
        F8StateSpec(
            name="allowNumpy",
            label="Allow Numpy",
            description="Enable `np.*` and `numpy.*` inside the expression.",
            uiControl="toggle",
            valueSchema=boolean_schema(default=False),
            access=F8StateAccess.rw,
            showOnNode=False,
            required=False,
        ),
        F8StateSpec(
            name="unpackDictOutputs",
            label="Unpack Dict Outputs",
            description="When enabled, dict results are unpacked into output ports with matching names.",
            uiControl="toggle",
            valueSchema=boolean_schema(default=False),
            access=F8StateAccess.rw,
            showOnNode=False,
            required=False,
        ),
        F8StateSpec(
            name="code",
            label="Expr",
            description=(
                "Single Python expression using input port names directly. Intended for quick local graph transforms "
                "without deploying a remote engine service. Use this when you want immediate editor-side value shaping."
            ),
            uiControl="wrapline[python]",
            valueSchema=string_schema(default="x"),
            access=F8StateAccess.rw,
            showOnNode=True,
            required=False,
        ),
    ],
)


def register_operator(registry: RuntimeNodeRegistry | None = None) -> RuntimeNodeRegistry:
    reg = registry or RuntimeNodeRegistry.instance()

    def _factory(node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any]) -> OperatorNode:
        return DataExprRuntimeNode(node_id=node_id, node=node, initial_state=initial_state)

    reg.register(SERVICE_CLASS, OPERATOR_CLASS, _factory, overwrite=True)
    reg.register_operator_spec(DataExprRuntimeNode.SPEC, overwrite=True)
    return reg

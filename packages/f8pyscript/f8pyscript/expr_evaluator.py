from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from types import CodeType
from typing import Any

try:
    import numpy as np  # type: ignore
except ModuleNotFoundError:
    np = None  # type: ignore[assignment]


_PYEXPR_EVAL_ERRORS = (Exception,)
_ALLOWED_GLOBAL_FNS: dict[str, Any] = {
    "abs": abs,
    "float": float,
    "int": int,
    "min": min,
    "max": max,
    "round": round,
}

_ALLOWED_MATH_FNS: set[str] = {
    "sin",
    "cos",
    "tan",
    "asin",
    "acos",
    "atan",
    "atan2",
    "sqrt",
    "log",
    "log10",
    "exp",
    "floor",
    "ceil",
}


def wrap_pyexpr_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return _JsonRef(value)
    return value


@dataclass(frozen=True)
class _JsonRef:
    value: Any

    def __getattr__(self, name: str) -> Any:
        attr = str(name or "")
        if not attr or attr.startswith("_"):
            raise AttributeError(name)
        if isinstance(self.value, dict) and attr in self.value:
            return wrap_pyexpr_value(self.value[attr])
        raise AttributeError(name)

    def __getitem__(self, key: Any) -> Any:
        if isinstance(self.value, dict):
            if isinstance(key, str) and key.startswith("_"):
                raise KeyError(key)
            return wrap_pyexpr_value(self.value[key])
        if isinstance(self.value, (list, tuple)):
            return wrap_pyexpr_value(self.value[int(key)])
        raise TypeError(f"not indexable: {type(self.value).__name__}")

    def __iter__(self):
        if isinstance(self.value, dict):
            for key in self.value:
                yield key
            return
        if isinstance(self.value, (list, tuple)):
            for item in self.value:
                yield wrap_pyexpr_value(item)
            return
        raise TypeError(f"not iterable: {type(self.value).__name__}")

    def unwrap(self) -> Any:
        if isinstance(self.value, dict):
            return {str(key): _JsonRef(item).unwrap() for key, item in self.value.items()}
        if isinstance(self.value, list):
            return [_JsonRef(item).unwrap() for item in self.value]
        if isinstance(self.value, tuple):
            return tuple(_JsonRef(item).unwrap() for item in self.value)
        return self.value


class _ExprValidator(ast.NodeVisitor):
    def __init__(self, *, allow_numpy: bool) -> None:
        super().__init__()
        self._allow_numpy = bool(allow_numpy)
        self._errors: list[str] = []

    def error(self, message: str) -> None:
        self._errors.append(str(message))

    def validate(self, expr: str) -> tuple[ast.Expression | None, str | None]:
        try:
            tree = ast.parse(str(expr or ""), mode="eval")
        except SyntaxError as exc:
            return None, f"syntax error: {exc.msg}"
        self.visit(tree)
        if self._errors:
            return None, "; ".join(self._errors[:3])
        if not isinstance(tree, ast.Expression):
            return None, "not an expression"
        return tree, None

    def generic_visit(self, node: ast.AST) -> Any:
        allowed: tuple[type[ast.AST], ...] = (
            ast.Expression,
            ast.Name,
            ast.Load,
            ast.Constant,
            ast.Attribute,
            ast.Subscript,
            ast.Slice,
            ast.Tuple,
            ast.List,
            ast.Dict,
            ast.UnaryOp,
            ast.UAdd,
            ast.USub,
            ast.Not,
            ast.BinOp,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.FloorDiv,
            ast.Mod,
            ast.Pow,
            ast.BoolOp,
            ast.And,
            ast.Or,
            ast.Compare,
            ast.Eq,
            ast.NotEq,
            ast.Lt,
            ast.LtE,
            ast.Gt,
            ast.GtE,
            ast.Is,
            ast.IsNot,
            ast.In,
            ast.NotIn,
            ast.IfExp,
            ast.comprehension,
            ast.ListComp,
            ast.SetComp,
            ast.DictComp,
            ast.GeneratorExp,
            ast.Store,
            ast.Call,
            ast.keyword,
        )
        if not isinstance(node, allowed):
            self.error(f"disallowed syntax: {type(node).__name__}")
            return None
        return super().generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        if str(node.attr or "").startswith("_"):
            self.error("private/dunder attribute access is not allowed")
            return None
        return self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        if isinstance(node.func, ast.Name):
            if str(node.func.id) not in _ALLOWED_GLOBAL_FNS:
                self.error(f"call not allowed: {node.func.id}")
                return None
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "math":
            fn = str(node.func.attr or "")
            if fn not in _ALLOWED_MATH_FNS:
                self.error(f"math call not allowed: math.{fn}")
                return None
        elif isinstance(node.func, ast.Attribute):
            base: ast.AST = node.func.value
            while isinstance(base, ast.Attribute):
                base = base.value
            if not self._allow_numpy:
                self.error("numpy calls are disabled")
                return None
            if not (isinstance(base, ast.Name) and base.id in ("np", "numpy")):
                self.error("call target not allowed")
                return None
        else:
            self.error("call target not allowed")
            return None
        return self.generic_visit(node)


def compile_pyexpr(expr: str, *, allow_numpy: bool) -> tuple[CodeType | None, str | None]:
    validator = _ExprValidator(allow_numpy=allow_numpy)
    tree, error = validator.validate(expr)
    if tree is None:
        return None, str(error or "invalid expression")
    try:
        return compile(tree, "<f8.pyexpr>", "eval"), None
    except (SyntaxError, TypeError, ValueError) as exc:
        return None, str(exc)


def _safe_eval_compiled(code: CodeType, *, names: dict[str, Any], allow_numpy: bool) -> Any:
    safe_globals: dict[str, Any] = {"__builtins__": {}}
    safe_globals.update(_ALLOWED_GLOBAL_FNS)
    safe_globals["math"] = math
    if allow_numpy:
        if np is None:
            raise RuntimeError("numpy is not available")
        safe_globals["np"] = np
        safe_globals["numpy"] = np
    return eval(code, safe_globals, names)  # noqa: S307


@dataclass(frozen=True, slots=True)
class PyExprEvalResult:
    value: Any = None
    error: BaseException | None = None


class PyExprEvaluator:
    def evaluate(self, code: CodeType, *, names: dict[str, Any], allow_numpy: bool) -> PyExprEvalResult:
        try:
            value = _safe_eval_compiled(code, names=names, allow_numpy=allow_numpy)
            if isinstance(value, _JsonRef):
                value = value.unwrap()
            return PyExprEvalResult(value=value)
        except _PYEXPR_EVAL_ERRORS as exc:
            return PyExprEvalResult(error=exc)


__all__ = ["PyExprEvalResult", "PyExprEvaluator", "compile_pyexpr", "np", "wrap_pyexpr_value"]

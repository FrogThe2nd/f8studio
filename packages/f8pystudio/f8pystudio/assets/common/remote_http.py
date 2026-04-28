from __future__ import annotations

import http.cookies
import json
import zlib
from typing import Protocol

from .common import JsonObject


class HttpHeadersLike(Protocol):
    def get(self, name: str, default: str = "") -> str: ...

    def get_all(self, name: str) -> list[str] | None: ...


class HttpResponseLike(Protocol):
    status: int
    headers: HttpHeadersLike

    def read(self) -> bytes: ...


class HttpResponseContext(Protocol):
    def __enter__(self) -> HttpResponseLike: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...


def build_json_request_data(
    payload: JsonObject | None,
    *,
    compress_threshold_bytes: int = 4096,
) -> tuple[bytes | None, dict[str, str]]:
    if payload is None:
        return None, {}
    raw_json = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(raw_json) <= compress_threshold_bytes:
        return raw_json, {}
    return zlib.compress(raw_json, level=6, wbits=31), {"Content-Encoding": "gzip"}


def header_values(headers: HttpHeadersLike, name: str) -> list[str]:
    values = headers.get_all(name)
    if values:
        return [str(value) for value in values if str(value).strip()]
    value = headers.get(name, "")
    if not value:
        return []
    return [str(value)]


def session_cookie_from_headers(headers: HttpHeadersLike) -> str:
    values = header_values(headers, "Set-Cookie")
    if not values:
        return ""
    cookie_jar = http.cookies.SimpleCookie()
    for value in values:
        try:
            cookie_jar.load(value)
        except http.cookies.CookieError:
            continue
    if not cookie_jar:
        return ""
    return "; ".join(f"{morsel.key}={morsel.value}" for morsel in cookie_jar.values())


__all__ = [
    "HttpHeadersLike",
    "HttpResponseContext",
    "HttpResponseLike",
    "build_json_request_data",
    "header_values",
    "session_cookie_from_headers",
]

"""HTTP helpers for provider configuration requests."""
from __future__ import annotations

import json
from urllib import error

STUDIO_API_USER_AGENT = "F8PyStudio/0.4 API Client"


def api_request_headers(*, content_type: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": STUDIO_API_USER_AGENT,
    }
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def format_http_error(exc: error.HTTPError) -> str:
    body = _read_http_error_body(exc)
    cloudflare_message = _format_cloudflare_error(exc.code, body)
    if cloudflare_message:
        return cloudflare_message
    if not body:
        return f"HTTP {exc.code}: {exc.reason}"
    return f"HTTP {exc.code}: {_truncate(body)}"


def _read_http_error_body(exc: error.HTTPError) -> str:
    return exc.read().decode("utf-8", errors="replace").strip()


def _format_cloudflare_error(status_code: int, body: str) -> str:
    if not body:
        return ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""

    cloudflare_error = bool(payload.get("cloudflare_error", False))
    error_code_raw = payload.get("error_code")
    error_name_raw = payload.get("error_name")
    error_category_raw = payload.get("error_category")
    detail_raw = payload.get("detail")
    ray_id_raw = payload.get("ray_id")

    if not cloudflare_error and error_code_raw is None and error_name_raw is None:
        return ""

    error_code = str(error_code_raw or "").strip()
    error_name = str(error_name_raw or "").strip()
    error_category = str(error_category_raw or "").strip()
    detail = str(detail_raw or "").strip()
    ray_id = str(ray_id_raw or "").strip()

    label_parts: list[str] = []
    if error_code:
        label_parts.append(f"error {error_code}")
    if error_name:
        label_parts.append(error_name)
    if error_category:
        label_parts.append(error_category)
    label = " / ".join(label_parts) if label_parts else "Cloudflare access rule"

    message = (
        f"HTTP {status_code}: Cloudflare blocked the provider endpoint ({label}). "
        "Configure Cloudflare to skip browser/bot checks for the API path, or use an origin endpoint that allows API clients."
    )
    if detail:
        message = f"{message} Detail: {detail}"
    if ray_id:
        message = f"{message} Ray ID: {ray_id}"
    return message


def _truncate(text: str) -> str:
    if len(text) <= 500:
        return text
    return text[:497] + "..."

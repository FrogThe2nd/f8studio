from __future__ import annotations

from email.message import Message
from io import BytesIO
import json
from typing import Any
from urllib import error

from f8pystudio.agents.provider_http import format_http_error


def _fake_http_error(*, code: int, body: dict[str, Any] | str, reason: str = "Error") -> error.HTTPError:
    if isinstance(body, str):
        raw_body = body.encode("utf-8")
    else:
        raw_body = json.dumps(body).encode("utf-8")
    return error.HTTPError(
        url="https://example.test/v1/models",
        code=code,
        msg=reason,
        hdrs=Message(),
        fp=BytesIO(raw_body),
    )


def test_format_http_error_summarizes_cloudflare_1010_payload() -> None:
    message = format_http_error(
        _fake_http_error(
            code=403,
            body={
                "cloudflare_error": True,
                "error_code": 1010,
                "error_name": "browser_signature_banned",
                "error_category": "access_denied",
                "detail": "The site owner has blocked access based on your browser's signature.",
                "ray_id": "a0698775feb0b165",
            },
            reason="Forbidden",
        )
    )

    assert message.startswith("HTTP 403: Cloudflare blocked the provider endpoint")
    assert "error 1010 / browser_signature_banned / access_denied" in message
    assert "skip browser/bot checks for the API path" in message
    assert "a0698775feb0b165" in message

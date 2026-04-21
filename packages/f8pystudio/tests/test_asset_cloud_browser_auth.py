from __future__ import annotations

from urllib import parse, request

import pytest

from f8pystudio.assets.common import (
    AssetCloudBrowserAuthError,
    AssetCloudBrowserCallbackServer,
    build_browser_callback_redirect_url,
    build_pkce_code_challenge,
    create_browser_auth_session,
)


def test_create_browser_auth_session_builds_loopback_redirect_uri() -> None:
    session = create_browser_auth_session(base_url="https://assetcloud.feel8.fun/")

    assert session.base_url == "https://assetcloud.feel8.fun"
    assert session.client_id == "pystudio"
    assert session.redirect_uri.startswith("http://127.0.0.1:")
    assert session.redirect_uri.endswith("/callback")
    assert session.authorize_url.startswith("https://assetcloud.feel8.fun/v1/auth/desktop/authorize?")
    authorize_url = parse.urlsplit(session.authorize_url)
    query = parse.parse_qs(authorize_url.query)
    assert query["client_id"] == ["pystudio"]
    assert query["redirect_uri"] == [session.redirect_uri]
    assert query["state"] == [session.state]
    assert query["code_challenge"] == [session.code_challenge]
    assert query["code_challenge_method"] == ["S256"]
    assert session.code_challenge == build_pkce_code_challenge(session.code_verifier)


def test_browser_callback_server_returns_authorization_code() -> None:
    session = create_browser_auth_session(base_url="https://assetcloud.feel8.fun")
    server = AssetCloudBrowserCallbackServer(
        callback_port=session.callback_port,
        success_redirect_url=build_browser_callback_redirect_url(base_url=session.base_url, success=True),
    )
    server.start()
    try:
        response = request.urlopen(f"{session.redirect_uri}?code=desktop-code-1&state={session.state}", timeout=5)
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "Sign-in complete" in body
        assert "window.close()" in body
        assert "/auth-complete" in body

        callback = server.wait_for_callback(timeout_seconds=1.0)
        assert callback.code == "desktop-code-1"
        assert callback.state == session.state
    finally:
        server.stop()


def test_browser_callback_server_surfaces_error_callback() -> None:
    session = create_browser_auth_session(base_url="https://assetcloud.feel8.fun")
    server = AssetCloudBrowserCallbackServer(
        callback_port=session.callback_port,
        error_redirect_url=build_browser_callback_redirect_url(base_url=session.base_url, success=False),
    )
    server.start()
    try:
        with pytest.raises(Exception) as exc_info:
            _ = request.urlopen(
                f"{session.redirect_uri}?error=access_denied&error_description=User%20cancelled&state={session.state}",
                timeout=5,
            )
        body = exc_info.value.read().decode("utf-8")
        assert "Sign-in failed" in body
        assert "/auth-error" in body
        with pytest.raises(AssetCloudBrowserAuthError, match="User cancelled"):
            _ = server.wait_for_callback(timeout_seconds=1.0)
    finally:
        server.stop()

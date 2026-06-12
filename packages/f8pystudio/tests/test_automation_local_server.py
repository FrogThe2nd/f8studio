from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import pytest

from f8pystudio.automation.client import load_connection_info, wait_for_connection_file
from f8pystudio.automation.cli import main as automation_cli_main
from f8pystudio.automation.control_protocol import AutomationConnectionInfo
from f8pystudio.automation.local_server import LocalAutomationServer


def _roundtrip(port: int, payload: dict[str, object]) -> dict[str, object]:
    raw = (json.dumps(payload) + "\n").encode("utf-8")
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
        sock.sendall(raw)
        data = sock.recv(4096)
    out = json.loads(data.decode("utf-8"))
    assert isinstance(out, dict)
    return out


def test_local_automation_server_authenticates_and_dispatches() -> None:
    def handle(method: str, params: dict[str, object]) -> dict[str, object]:
        return {"method": method, "value": params.get("value")}

    server = LocalAutomationServer(token="secret", request_handler=handle)
    server.start()
    try:
        response = _roundtrip(
            server.port,
            {"requestId": "r1", "method": "demo.echo", "token": "secret", "params": {"value": 42}},
        )
    finally:
        server.stop()

    assert response["ok"] is True
    assert response["result"] == {"method": "demo.echo", "value": 42}


def test_local_automation_server_allows_success_payload_with_error_field() -> None:
    def handle(method: str, params: dict[str, object]) -> dict[str, object]:
        _ = method
        _ = params
        return {"submitted": True, "completed": True, "error": ""}

    server = LocalAutomationServer(token="secret", request_handler=handle)
    server.start()
    try:
        response = _roundtrip(
            server.port,
            {"requestId": "r1", "method": "runtime.debugData", "token": "secret", "params": {}},
        )
    finally:
        server.stop()

    assert response["ok"] is True
    assert response["result"] == {"submitted": True, "completed": True, "error": ""}


def test_local_automation_server_rejects_bad_token() -> None:
    server = LocalAutomationServer(token="secret", request_handler=lambda _method, _params: {})
    server.start()
    try:
        response = _roundtrip(
            server.port,
            {"requestId": "r1", "method": "demo.echo", "token": "wrong", "params": {}},
        )
    finally:
        server.stop()

    assert response["ok"] is False
    assert response["error"]["code"] == "unauthorized"


def test_connection_info_serializes_as_client_protocol_camel_case(tmp_path: Path) -> None:
    info = AutomationConnectionInfo(
        pid=123,
        host="127.0.0.1",
        port=456,
        token_file=str(tmp_path / "token"),
        studio_service_id="studio",
        created_at=789,
    )

    assert info.to_dict() == {
        "pid": 123,
        "host": "127.0.0.1",
        "port": 456,
        "tokenFile": str(tmp_path / "token"),
        "studioServiceId": "studio",
        "createdAt": 789,
    }


def test_load_connection_info_accepts_legacy_snake_case_metadata(tmp_path: Path) -> None:
    connection_file = tmp_path / "connection.json"
    connection_file.write_text(
        json.dumps(
            {
                "pid": 123,
                "host": "127.0.0.1",
                "port": 456,
                "token_file": str(tmp_path / "token"),
                "studio_service_id": "studio",
                "created_at": 789,
            }
        ),
        encoding="utf-8",
    )

    info = load_connection_info(connection_file)

    assert info.pid == 123
    assert info.host == "127.0.0.1"
    assert info.port == 456
    assert info.token_file == str(tmp_path / "token")
    assert info.studio_service_id == "studio"
    assert info.created_at == 789


def test_load_connection_info_requires_token_file(tmp_path: Path) -> None:
    connection_file = tmp_path / "connection.json"
    connection_file.write_text(
        json.dumps({"pid": 123, "host": "127.0.0.1", "port": 456}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="tokenFile"):
        load_connection_info(connection_file)


def test_wait_for_connection_file_ignores_stale_metadata(tmp_path: Path) -> None:
    connection_file = tmp_path / "connection.json"
    connection_file.write_text(
        json.dumps(
            {
                "pid": 1,
                "host": "127.0.0.1",
                "port": 9999,
                "tokenFile": str(tmp_path / "token"),
                "studioServiceId": "studio_old",
                "createdAt": 10,
            }
        ),
        encoding="utf-8",
    )
    stale_mtime_ns = int(connection_file.stat().st_mtime_ns)

    with pytest.raises(TimeoutError):
        wait_for_connection_file(
            connection_file,
            timeout_s=0.1,
            min_created_at=int(time.time()) + 100,
            previous_mtime_ns=stale_mtime_ns,
        )


def test_automation_cli_project_commands_forward_to_client(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[str, dict[str, object] | None]] = []

    class FakeClient:
        def call(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
            calls.append((method, params))
            return {"ok": True}

    monkeypatch.setattr(
        "f8pystudio.automation.cli.AutomationClient.from_connection_file",
        lambda path: FakeClient(),
    )

    assert automation_cli_main(["--connection-file", "demo.json", "project", "list"]) == 0
    assert automation_cli_main(["project", "new", "--confirm", "--keep-current-project"]) == 0
    assert (
        automation_cli_main(
            [
                "project",
                "save",
                "--name",
                "VAM Gen 2",
                "--description",
                "motion graph",
                "--tag",
                "vam",
                "--project-id",
                "project-a",
                "--overwrite-project-id",
                "project-b",
            ]
        )
        == 0
    )
    assert automation_cli_main(["project", "load", "--project-id", "project-a", "--confirm"]) == 0

    _ = capsys.readouterr()
    assert calls == [
        ("project.list", None),
        ("project.new", {"confirm": True, "clearCurrentProject": False}),
        (
            "project.save",
            {
                "name": "VAM Gen 2",
                "description": "motion graph",
                "tags": ["vam"],
                "projectId": "project-a",
                "overwriteProjectId": "project-b",
            },
        ),
        ("project.load", {"projectId": "project-a", "confirm": True}),
    ]

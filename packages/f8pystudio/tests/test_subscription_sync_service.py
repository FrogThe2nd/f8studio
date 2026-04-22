from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from qtpy import QtCore, QtTest, QtWidgets
from f8pysdk.codec import copy_model
from f8pysdk.specs import F8VariantKind, F8VariantRecord

from f8pystudio.assets.components.component_models import (
    F8ComponentEntry,
    F8ComponentRecord,
    F8ComponentRemoteListPage,
    F8ComponentRemoteUser,
    F8ComponentSourceKind,
    F8ComponentVisibility,
)
from f8pystudio.assets.subscriptions import SubscriptionSyncService
from f8pystudio.assets.variants.variant_models import (
    F8VariantEntry,
    F8VariantRemoteListPage,
    F8VariantRemoteUser,
    F8VariantSourceKind,
    F8VariantVisibility,
    variant_now_iso,
)


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _wait_until(predicate, *, timeout_ms: int = 3000) -> None:
    deadline = time.monotonic() + (float(timeout_ms) / 1000.0)
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        if predicate():
            return
        QtTest.QTest.qWait(10)
    QtWidgets.QApplication.processEvents()
    assert predicate()


def _spy_count(spy: QtTest.QSignalSpy) -> int:
    return int(spy.count())


def _variant_entry(
    *,
    variant_id: str,
    version_number: int,
    installed: bool,
    subscribed: bool = True,
) -> F8VariantEntry:
    now = variant_now_iso()
    return F8VariantEntry(
        record=F8VariantRecord(
            variantId=variant_id,
            kind=F8VariantKind.operator,
            baseNodeType="svc.base.op",
            serviceClass="svc.test",
            operatorClass="svc.operator",
            name=f"Variant {variant_id}",
            description="",
            tags=[],
            spec={"schemaVersion": "spec/1", "label": variant_id},
            createdAt=now,
            updatedAt=now,
        ),
        source=F8VariantSourceKind.remote_public,
        visibility=F8VariantVisibility.public,
        ownerUserId="owner-1",
        ownerDisplayName="Owner",
        remoteVersionNumber=version_number,
        installed=installed,
        hasCachedContent=installed,
        subscribed=subscribed,
    )


def _component_entry(
    *,
    component_id: str,
    version_number: int,
    installed: bool,
    subscribed: bool = True,
) -> F8ComponentEntry:
    now = variant_now_iso()
    return F8ComponentEntry(
        record=F8ComponentRecord(
            componentId=component_id,
            name=f"Component {component_id}",
            description="",
            tags=[],
            content={
                "schemaVersion": "f8studio-session/1",
                "layout": {"nodes": {}, "connections": []},
            },
            createdAt=now,
            updatedAt=now,
        ),
        source=F8ComponentSourceKind.remote_public,
        visibility=F8ComponentVisibility.public,
        ownerUserId="owner-1",
        ownerDisplayName="Owner",
        remoteVersionNumber=version_number,
        installed=installed,
        hasCachedContent=installed,
        subscribed=subscribed,
    )


@dataclass
class _FakeVariantClient:
    user: F8VariantRemoteUser | None
    page_sequences: list[list[F8VariantEntry]]
    remote_entries: dict[str, F8VariantEntry]
    install_started: list[str]
    install_completed: list[str]
    fail_install_ids: set[str]
    gate_install_by_id: dict[str, threading.Event]
    refresh_calls: int = 0

    def clone_for_background(self) -> _FakeVariantClient:
        return self

    def current_user(self) -> F8VariantRemoteUser | None:
        return self.user

    def refresh_scope_page(
        self,
        *,
        scope: str,
        kind: str = "",
        base_node_type: str = "",
        query: str = "",
        cursor: str = "",
        append: bool = False,
    ) -> F8VariantRemoteListPage:
        del kind
        del base_node_type
        del query
        del append
        assert scope == "subscribed"
        index = 0 if not cursor else int(cursor)
        self.refresh_calls += 1
        entries = self.page_sequences[index] if index < len(self.page_sequences) else []
        next_cursor = None if index + 1 >= len(self.page_sequences) else str(index + 1)
        for entry in entries:
            existing = self.remote_entries.get(str(entry.record.variantId))
            if existing is not None and existing.installed and existing.remoteVersionNumber == entry.remoteVersionNumber:
                self.remote_entries[str(entry.record.variantId)] = copy_model(
                    entry,
                    update={
                        "installed": True,
                        "hasCachedContent": True,
                        "downloadedAt": existing.downloadedAt or variant_now_iso(),
                    },
                )
            else:
                self.remote_entries[str(entry.record.variantId)] = copy_model(
                    entry,
                    update={"installed": False, "hasCachedContent": False},
                )
        return F8VariantRemoteListPage(entries=list(entries), nextCursor=next_cursor)

    def remote_entry(self, variant_id: str) -> F8VariantEntry | None:
        return self.remote_entries.get(str(variant_id))

    def install_variant(self, variant_id: str) -> F8VariantEntry:
        normalized_variant_id = str(variant_id)
        self.install_started.append(normalized_variant_id)
        gate = self.gate_install_by_id.get(normalized_variant_id)
        if gate is not None:
            gate.wait(timeout=2.0)
        if normalized_variant_id in self.fail_install_ids:
            raise RuntimeError(f"failed to install {normalized_variant_id}")
        entry = self.remote_entries[normalized_variant_id]
        installed_entry = copy_model(
            entry,
            update={"installed": True, "hasCachedContent": True, "downloadedAt": variant_now_iso()},
        )
        self.remote_entries[normalized_variant_id] = installed_entry
        self.install_completed.append(normalized_variant_id)
        return installed_entry


@dataclass
class _FakeComponentClient:
    user: F8ComponentRemoteUser | None
    page_sequences: list[list[F8ComponentEntry]]
    remote_entries: dict[str, F8ComponentEntry]
    install_started: list[str]
    install_completed: list[str]
    fail_install_ids: set[str]
    gate_install_by_id: dict[str, threading.Event]
    refresh_calls: int = 0

    def clone_for_background(self) -> _FakeComponentClient:
        return self

    def current_user(self) -> F8ComponentRemoteUser | None:
        return self.user

    def refresh_scope_page(
        self,
        *,
        scope: str,
        query: str = "",
        cursor: str = "",
        append: bool = False,
    ) -> F8ComponentRemoteListPage:
        del query
        del append
        assert scope == "subscribed"
        index = 0 if not cursor else int(cursor)
        self.refresh_calls += 1
        entries = self.page_sequences[index] if index < len(self.page_sequences) else []
        next_cursor = None if index + 1 >= len(self.page_sequences) else str(index + 1)
        for entry in entries:
            existing = self.remote_entries.get(str(entry.record.componentId))
            if existing is not None and existing.installed and existing.remoteVersionNumber == entry.remoteVersionNumber:
                self.remote_entries[str(entry.record.componentId)] = copy_model(
                    entry,
                    update={
                        "installed": True,
                        "hasCachedContent": True,
                        "downloadedAt": existing.downloadedAt or variant_now_iso(),
                    },
                )
            else:
                self.remote_entries[str(entry.record.componentId)] = copy_model(
                    entry,
                    update={"installed": False, "hasCachedContent": False},
                )
        return F8ComponentRemoteListPage(entries=list(entries), nextCursor=next_cursor)

    def remote_entry(self, component_id: str) -> F8ComponentEntry | None:
        return self.remote_entries.get(str(component_id))

    def install_component(self, component_id: str) -> F8ComponentEntry:
        normalized_component_id = str(component_id)
        self.install_started.append(normalized_component_id)
        gate = self.gate_install_by_id.get(normalized_component_id)
        if gate is not None:
            gate.wait(timeout=2.0)
        if normalized_component_id in self.fail_install_ids:
            raise RuntimeError(f"failed to install {normalized_component_id}")
        entry = self.remote_entries[normalized_component_id]
        installed_entry = copy_model(
            entry,
            update={"installed": True, "hasCachedContent": True, "downloadedAt": variant_now_iso()},
        )
        self.remote_entries[normalized_component_id] = installed_entry
        self.install_completed.append(normalized_component_id)
        return installed_entry


def _make_service(
    *,
    variant_pages: list[list[F8VariantEntry]] | None = None,
    component_pages: list[list[F8ComponentEntry]] | None = None,
    existing_variants: dict[str, F8VariantEntry] | None = None,
    existing_components: dict[str, F8ComponentEntry] | None = None,
    logged_in: bool = True,
    variant_fail_install_ids: set[str] | None = None,
    component_fail_install_ids: set[str] | None = None,
    variant_gate_install_by_id: dict[str, threading.Event] | None = None,
    component_gate_install_by_id: dict[str, threading.Event] | None = None,
) -> tuple[SubscriptionSyncService, _FakeVariantClient, _FakeComponentClient]:
    variant_user = None if not logged_in else F8VariantRemoteUser(userId="user-1", name="Alice", email="alice@example.com")
    component_user = None if not logged_in else F8ComponentRemoteUser(userId="user-1", name="Alice", email="alice@example.com")
    variant_client = _FakeVariantClient(
        user=variant_user,
        page_sequences=[[]] if variant_pages is None else variant_pages,
        remote_entries={} if existing_variants is None else dict(existing_variants),
        install_started=[],
        install_completed=[],
        fail_install_ids=set() if variant_fail_install_ids is None else set(variant_fail_install_ids),
        gate_install_by_id={} if variant_gate_install_by_id is None else dict(variant_gate_install_by_id),
    )
    component_client = _FakeComponentClient(
        user=component_user,
        page_sequences=[[]] if component_pages is None else component_pages,
        remote_entries={} if existing_components is None else dict(existing_components),
        install_started=[],
        install_completed=[],
        fail_install_ids=set() if component_fail_install_ids is None else set(component_fail_install_ids),
        gate_install_by_id={} if component_gate_install_by_id is None else dict(component_gate_install_by_id),
    )
    service = SubscriptionSyncService(variant_client=variant_client, component_client=component_client)
    return service, variant_client, component_client


def test_subscription_sync_service_only_enqueues_diffed_assets() -> None:
    _ensure_app()
    matched_variant = _variant_entry(variant_id="variant-same", version_number=1, installed=True)
    matched_component = _component_entry(component_id="component-same", version_number=1, installed=True)
    service, variant_client, component_client = _make_service(
        variant_pages=[[copy_model(matched_variant, update={"installed": False, "hasCachedContent": False})]],
        component_pages=[[copy_model(matched_component, update={"installed": False, "hasCachedContent": False})]],
        existing_variants={"variant-same": matched_variant},
        existing_components={"component-same": matched_component},
    )
    started_spy = QtTest.QSignalSpy(service.sync_started)
    finished_spy = QtTest.QSignalSpy(service.sync_finished)

    service.start_initial_sync()

    _wait_until(lambda: _spy_count(finished_spy) == 1)
    assert _spy_count(started_spy) == 1
    assert list(started_spy.at(0)) == [0]
    assert list(finished_spy.at(0)) == [0, 0, 2]
    assert variant_client.install_started == []
    assert component_client.install_started == []


def test_subscription_sync_service_processes_installations_serially() -> None:
    _ensure_app()
    gate = threading.Event()
    service, variant_client, component_client = _make_service(
        variant_pages=[[_variant_entry(variant_id="variant-a", version_number=1, installed=False)]],
        component_pages=[[_component_entry(component_id="component-a", version_number=1, installed=False)]],
        variant_gate_install_by_id={"variant-a": gate},
    )
    finished_spy = QtTest.QSignalSpy(service.sync_finished)

    service.start_initial_sync()

    _wait_until(lambda: variant_client.install_started == ["variant-a"])
    assert component_client.install_started == []

    gate.set()

    _wait_until(lambda: _spy_count(finished_spy) == 1)
    assert variant_client.install_completed == ["variant-a"]
    assert component_client.install_completed == ["component-a"]


def test_subscription_sync_service_continues_after_item_failure() -> None:
    _ensure_app()
    service, variant_client, component_client = _make_service(
        variant_pages=[[_variant_entry(variant_id="variant-fail", version_number=1, installed=False)]],
        component_pages=[[_component_entry(component_id="component-ok", version_number=1, installed=False)]],
        variant_fail_install_ids={"variant-fail"},
    )
    failed_spy = QtTest.QSignalSpy(service.sync_item_failed)
    finished_spy = QtTest.QSignalSpy(service.sync_finished)

    service.start_initial_sync()

    _wait_until(lambda: _spy_count(finished_spy) == 1)
    assert _spy_count(failed_spy) == 1
    assert list(failed_spy.at(0))[0] == "variant-fail"
    assert variant_client.install_started == ["variant-fail"]
    assert component_client.install_completed == ["component-ok"]
    assert list(finished_spy.at(0)) == [1, 1, 0]


def test_subscription_sync_service_is_noop_when_logged_out() -> None:
    _ensure_app()
    service, _, _ = _make_service(logged_in=False)
    started_spy = QtTest.QSignalSpy(service.sync_started)
    finished_spy = QtTest.QSignalSpy(service.sync_finished)

    service.start_initial_sync()
    QtTest.QTest.qWait(80)
    QtWidgets.QApplication.processEvents()

    assert _spy_count(started_spy) == 0
    assert _spy_count(finished_spy) == 0
    assert service.is_running() is False


def test_subscription_sync_service_collapses_reentrant_manual_refreshes() -> None:
    _ensure_app()
    gate = threading.Event()
    service, variant_client, _component_client = _make_service(
        variant_pages=[[_variant_entry(variant_id="variant-a", version_number=1, installed=False)]],
        component_pages=[[]],
        variant_gate_install_by_id={"variant-a": gate},
    )
    started_spy = QtTest.QSignalSpy(service.sync_started)
    finished_spy = QtTest.QSignalSpy(service.sync_finished)

    service.request_manual_refresh()
    _wait_until(lambda: _spy_count(started_spy) == 1 and variant_client.install_started == ["variant-a"])

    service.request_manual_refresh()
    service.request_manual_refresh()
    gate.set()

    _wait_until(lambda: _spy_count(finished_spy) == 2)
    assert _spy_count(started_spy) == 2
    assert list(started_spy.at(0)) == [1]
    assert list(started_spy.at(1)) == [0]

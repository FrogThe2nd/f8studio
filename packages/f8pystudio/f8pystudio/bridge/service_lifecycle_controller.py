from __future__ import annotations

import asyncio
import time
from typing import Any

from qtpy import QtCore

from f8pysdk.bus import BusBackend
from f8pysdk.f8_naming import ensure_token

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS
from .process_lifecycle import StartServiceRequest, StopServiceRequest
from .service_endpoint_client import request_service_status, request_service_terminate, request_set_service_active
from f8pystudio.bridge.process_manager import ServiceProcessConfig
from f8pystudio.nodegraph.runtime_compiler import CompiledRuntimeGraphs


class ServiceLifecycleControllerMixin:
    def _runtime_bus_backend(self) -> BusBackend:
        return "zenoh"

    def _runtime_zenoh_config_path(self) -> str | None:
        return None

    def _runtime_zenoh_connect(self) -> tuple[str, ...]:
        return ()

    def _runtime_zenoh_listen(self) -> tuple[str, ...]:
        return ()

    def _runtime_zenoh_shm_pool_bytes(self) -> int:
        return 256 * 1024 * 1024

    def _runtime_supervision_mode(self) -> str:
        cfg = self._cfg
        if cfg is None:
            return "studio_owned"
        mode = str(cfg.supervision_mode or "studio_owned").strip().lower()
        if mode == "detached":
            return "detached"
        return "studio_owned"

    async def _ensure_requester(self) -> Any | None:
        return None

    def _stop_process_once_local(self, service_id: str) -> bool:
        sid = str(service_id or "").strip()
        if not sid:
            return False
        try:
            stop_result = self._process_gateway.stop(StopServiceRequest(service_id=sid))
            return bool(stop_result.success)
        except Exception as exc:
            self._emit_log_line(f"stop_service failed: {exc}")
            return False

    def set_managed_active(self, active: bool) -> None:
        """
        Activate/deactivate all managed service instances (via command channel).

        This is the lifecycle control described in `docs/design/pysdk-runtime.md`.
        """
        self._managed_active = bool(active)
        self._submit_async(
            self._set_managed_active_async(bool(self._managed_active)),
            context="submit set_managed_active failed",
        )

    def unmanage_service(self, service_id: str) -> None:
        """
        Remove a serviceId from the studio's managed set (UI bookkeeping only).

        This does not stop the process by itself.
        """
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        if sid == self.studio_service_id:
            return

        self._managed_service_ids.discard(sid)
        self._managed_service_classes.pop(sid, None)
        self._service_status_cache.pop(sid, None)
        self._service_alive_cache.pop(sid, None)
        self._service_liveliness_instances_by_service.pop(sid, None)
        self._service_status_req_s.pop(sid, None)
        self._service_status_inflight.discard(sid)
        self._monitor_center.drop_service(service_id=sid)
        self._process_actions.cancel(service_id=sid)

    def reclaim_service(self, service_id: str) -> None:
        """
        Best-effort reclamation for a serviceId that was removed from the canvas:
        terminate the process and drop it from the managed set.
        """
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        if sid == self.studio_service_id:
            return
        self._emit_log_line(f"reclaim service process serviceId={sid}")
        self.stop_service(sid)
        self.unmanage_service(sid)

    def is_service_running(self, service_id: str) -> bool:
        sid = str(service_id or "").strip()
        if not sid:
            return False
        if sid == self.studio_service_id:
            return self._studio_service_bus() is not None
        try:
            if bool(self._process_gateway.is_running(str(sid))):
                return True
        except Exception as exc:
            self._report_exception(f"check process running failed serviceId={sid}", exc)
        # If the service wasn't launched by this studio process, fall back to
        # a best-effort "alive" cache (refreshed via status endpoint).
        v = self._service_alive_cache.get(sid)
        if not v:
            return False
        alive, ts = v
        if not alive:
            return False
        # Consider alive cache fresh for a short window to avoid UI flicker.
        return (time.monotonic() - float(ts)) < 0.9

    def get_service_class(self, service_id: str) -> str:
        """
        Best-effort service identity lookup for UI display (eg log tabs).
        """
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return ""
        if sid == self.studio_service_id:
            return str(SERVICE_CLASS)
        return str(self._managed_service_classes.get(sid, "") or "")

    def _cache_service_active(self, service_id: str, active: bool | None) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        self._service_status_cache[sid] = (active, time.monotonic())
        self._monitor_center.update_service_status(service_id=sid, active=active)

    def _cache_service_alive(self, service_id: str, alive: bool) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        self._service_alive_cache[sid] = (bool(alive), time.monotonic())
        self._monitor_center.update_service_status(service_id=sid, alive=bool(alive))

    def get_cached_service_active(self, service_id: str) -> bool | None:
        """
        Return last known remote service active state (best-effort).
        """
        sid = str(service_id or "").strip()
        if not sid:
            return None
        v = self._service_status_cache.get(sid)
        if not v:
            return None
        return v[0]

    async def _request_service_status_async(self, service_id: str) -> dict[str, Any] | None:
        sid = ""
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return None
        requester = await self._ensure_requester()
        if requester is None:
            return None
        return await request_service_status(requester, service_id=sid, timeout_s=0.4)

    def _cache_status_identity(self, service_id: str, status: dict[str, Any]) -> None:
        sid = str(service_id or "").strip()
        if not sid:
            return
        if "active" in status:
            self._cache_service_active(sid, status.get("active"))
        service_class = str(status.get("serviceClass") or "").strip()
        runtime_instance_id = str(status.get("runtimeInstanceId") or "").strip()
        if service_class:
            self._managed_service_classes[sid] = service_class
        if runtime_instance_id:
            instances = self._service_liveliness_instances_by_service.setdefault(sid, set())
            instances.add(runtime_instance_id)

    def _identity_status_valid(self, status: dict[str, Any]) -> bool:
        if not bool(status.get("identityValid")):
            return False
        service_class = str(status.get("serviceClass") or "").strip()
        runtime_instance_id = str(status.get("runtimeInstanceId") or "").strip()
        return bool(service_class and runtime_instance_id)

    async def _live_runtime_instances(self, service_id: str) -> set[str]:
        sid = ensure_token(str(service_id), label="service_id")
        instances = await self._query_zenoh_service_liveliness_instances_async(sid)
        return set(instances)

    def _block_duplicate_runtime_instances(self, service_id: str, instances: set[str]) -> bool:
        sid = str(service_id or "").strip()
        if len(instances) <= 1:
            return False
        instance_text = ",".join(sorted(instances))
        self._emit_log_line(
            f"deploy blocked serviceId={sid}: duplicate runtime instances for serviceId={sid} "
            f"instances={instance_text}"
        )
        self._cache_service_alive(sid, True)
        return True

    def request_service_status(self, service_id: str) -> None:
        """
        Trigger a best-effort status refresh (async).
        """
        sid = ""
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        if sid == self.studio_service_id:
            running = self._studio_service_bus() is not None
            self._cache_service_alive(sid, running)
            self._cache_service_active(sid, True if running else None)
            self._monitor_center.update_service_status(service_id=sid, ready=running)
            return
        now = time.monotonic()
        last = float(self._service_status_req_s.get(sid, 0.0))
        if (now - last) < 0.25:
            return
        if sid in self._service_status_inflight:
            return
        self._service_status_inflight.add(sid)
        self._service_status_req_s[sid] = now

        async def _do() -> None:
            try:
                status = await self._request_service_status_async(sid)
                if not isinstance(status, dict):
                    # Fast "down" signal: if status endpoint doesn't respond, treat service
                    # as not running for UI purposes (it will flip back to True on next success).
                    self._cache_service_alive(sid, False)
                    self._cache_service_active(sid, None)
                    return
                self._cache_service_alive(sid, True)
                if "active" in status:
                    self._cache_service_active(sid, status.get("active"))
                self._cache_status_identity(sid, status)
            finally:
                self._service_status_inflight.discard(sid)

        submitted = self._submit_async(_do(), context=f"submit request_service_status failed serviceId={sid}")
        if not submitted:
            self._service_status_inflight.discard(sid)

    def _start_service_process_local(self, *, service_id: str, service_class: str) -> bool:
        sid = ensure_token(str(service_id), label="service_id")
        svc_class = str(service_class or "").strip()
        if not svc_class:
            self._emit_log_line(f"start_service ignored (unknown serviceClass): serviceId={sid}")
            return False
        try:
            self._process_gateway.start(
                StartServiceRequest(
                    config=ServiceProcessConfig(
                        service_class=str(svc_class),
                        service_id=sid,
                        supervision_mode=self._runtime_supervision_mode(),
                        bus_backend=self._runtime_bus_backend(),
                        zenoh_config_path=self._runtime_zenoh_config_path(),
                        zenoh_connect=self._runtime_zenoh_connect(),
                        zenoh_listen=self._runtime_zenoh_listen(),
                        zenoh_shm_pool_bytes=self._runtime_zenoh_shm_pool_bytes(),
                    ),
                    on_output=lambda _sid, line, _sid2=sid: self.service_output.emit(_sid2, str(line)),
                )
            )
        except Exception as exc:
            self._emit_log_line(f"start_service failed: {exc}")
            return False
        self._managed_service_ids.add(sid)
        self._managed_service_classes[sid] = str(svc_class)
        self._emit_service_process_state_safe(sid, bool(self.is_service_running(sid)))
        return True

    async def ensure_service_available(
        self,
        service_id: str,
        desired_service_class: str,
        *,
        local_known_service_class: str | None = None,
    ) -> bool:
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return False
        if sid == self.studio_service_id:
            return True
        desired_class = str(desired_service_class or "").strip()
        if not desired_class:
            self._emit_log_line(f"deploy blocked serviceId={sid}: missing desired serviceClass")
            return False

        local_running = False
        try:
            local_running = bool(self._process_gateway.is_running(sid))
        except Exception as exc:
            self._report_exception(f"check local process failed serviceId={sid}", exc)
        if local_running:
            instances = await self._live_runtime_instances(sid)
            if self._block_duplicate_runtime_instances(sid, instances):
                return False
            local_class = str(local_known_service_class or self._managed_service_classes.get(sid, "") or "").strip()
            if local_class and local_class != desired_class:
                self._emit_log_line(
                    f"deploy blocked serviceId={sid}: local process serviceClass collision "
                    f"running={local_class} desired={desired_class}"
                )
                return False
            status = await self._request_service_status_async(sid)
            if isinstance(status, dict):
                if not self._identity_status_valid(status):
                    self._emit_log_line(
                        f"deploy blocked serviceId={sid}: status endpoint uses old protocol without identity"
                    )
                    return False
                running_class = str(status.get("serviceClass") or "").strip()
                if running_class and running_class != desired_class:
                    self._emit_log_line(
                        f"deploy blocked serviceId={sid}: serviceClass collision "
                        f"running={running_class} desired={desired_class}"
                    )
                    return False
                self._cache_service_alive(sid, True)
                self._cache_status_identity(sid, status)
            self._managed_service_ids.add(sid)
            self._managed_service_classes[sid] = desired_class
            self._emit_log_line(f"reuse local service serviceId={sid} serviceClass={desired_class}")
            return True

        instances = await self._live_runtime_instances(sid)
        if self._block_duplicate_runtime_instances(sid, instances):
            return False

        if len(instances) == 1:
            status = await self._request_service_status_async(sid)
            if status is None:
                self._emit_log_line(f"service liveliness found but status unreachable; spawning serviceId={sid}")
                return self._start_service_process_local(service_id=sid, service_class=desired_class)
            if not self._identity_status_valid(status):
                self._emit_log_line(
                    f"deploy blocked serviceId={sid}: status endpoint uses old protocol without identity"
                )
                return False
            running_class = str(status.get("serviceClass") or "").strip()
            if running_class != desired_class:
                self._emit_log_line(
                    f"deploy blocked serviceId={sid}: serviceClass collision "
                    f"running={running_class or '<empty>'} desired={desired_class}"
                )
                return False
            self._cache_service_alive(sid, True)
            self._cache_status_identity(sid, status)
            self._managed_service_ids.add(sid)
            self._managed_service_classes[sid] = desired_class
            self._emit_log_line(f"reuse live service serviceId={sid} serviceClass={desired_class}")
            return True

        return self._start_service_process_local(service_id=sid, service_class=desired_class)

    async def _set_service_active_async(self, service_id: str, active: bool) -> bool:
        sid = ""
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return False
        requester = await self._ensure_requester()
        if requester is None:
            return False
        ok = await request_set_service_active(
            requester,
            service_id=sid,
            active=bool(active),
            attempts=2,
            timeout_s=0.5,
            retry_sleep_s=0.15,
        )
        if ok:
            self._cache_service_active(sid, bool(active))
            return True
        return False

    def set_service_active(self, service_id: str, active: bool) -> None:
        sid = ""
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        if sid == self.studio_service_id:
            return

        self._submit_async(
            self._set_service_active_async(sid, bool(active)),
            context=f"submit set_service_active failed serviceId={sid}",
        )

    async def _request_service_terminate_async(self, service_id: str) -> bool:
        """
        Ask a running service process to exit itself (graceful).

        This is best-effort and may fail if the service is not connected to the runtime bus yet.
        """
        sid = ""
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return False

        requester = await self._ensure_requester()
        if requester is None:
            return False
        return await request_service_terminate(
            requester,
            service_id=sid,
            attempts=2,
            timeout_s=0.4,
            retry_sleep_s=0.15,
        )

    def start_service(self, service_id: str, *, service_class: str | None = None) -> None:
        sid = ""
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        if sid == self.studio_service_id:
            return

        # Dedup: if Studio already believes the service is alive (via local proc tracking or a fresh
        # status ping), do not spawn another process on repeated deploy (e.g. repeated F5).
        if self.is_service_running(sid):
            self._emit_log_line(f"start_service ignored (already running): serviceId={sid}")
            return

        svc_class = self._managed_service_classes.get(sid, "") or str(service_class or "")
        if not svc_class:
            self._emit_log_line(f"start_service ignored (unknown serviceClass): serviceId={sid}")
            return
        self._start_service_process_local(service_id=sid, service_class=str(svc_class))

    def stop_service(self, service_id: str) -> None:
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        if sid == self.studio_service_id:
            return

        if not self.is_service_running(sid):
            self._emit_service_process_state_safe(sid, False)
            return

        # 1) Ask the service to terminate itself (best for GUI apps / child process trees).
        self._submit_async(
            self._request_service_terminate_async(sid),
            context=f"submit request_service_terminate failed serviceId={sid}",
        )

        # 2) Poll for graceful exit, then fall back to local kill-tree.
        self._process_actions.schedule_stop(service_id=sid, grace_s=2.2)

    @QtCore.Slot(str, object)
    def _on_restart_service_after_guard(self, service_id: str, service_class: object) -> None:
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        if sid == self.studio_service_id:
            return
        svc_class = str(service_class or "")
        self._process_actions.schedule_restart(service_id=sid, service_class=svc_class, grace_s=2.2)

    def restart_service(self, service_id: str, *, service_class: str | None = None) -> None:
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        if sid == self.studio_service_id:
            return

        svc_class = self._managed_service_classes.get(sid, "") or str(service_class or "")

        async def _guard_and_schedule_restart() -> None:
            instances = await self._live_runtime_instances(sid)
            if self._block_duplicate_runtime_instances(sid, instances):
                return
            await self._request_service_terminate_async(sid)
            self._restart_service_after_guard.emit(sid, svc_class)

        if not self.is_service_running(sid):
            self.start_service(sid, service_class=svc_class or None)
            return

        self._submit_async(
            _guard_and_schedule_restart(),
            context=f"submit restart_service failed serviceId={sid}",
        )

    def start_service_and_deploy(
        self, service_id: str, *, service_class: str | None = None, compiled: CompiledRuntimeGraphs | None = None
    ) -> None:
        """
        Start service process (if needed) and deploy last compiled rungraph for it (best-effort).
        """
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        if sid == self.studio_service_id:
            return
        self.start_service(sid, service_class=service_class)

        async def _do() -> None:
            await self._refresh_studio_runtime_async(compiled=compiled)
            await self._deploy_service_rungraph_async(sid, compiled=compiled)
            await self._set_service_active_async(sid, True)

        self._submit_async(_do(), context=f"submit start_service_and_deploy failed serviceId={sid}")

    def restart_service_and_deploy(
        self, service_id: str, *, service_class: str | None = None, compiled: CompiledRuntimeGraphs | None = None
    ) -> None:
        """
        Restart service (terminate -> start) and deploy last compiled rungraph for it (best-effort).
        """
        try:
            sid = ensure_token(str(service_id), label="service_id")
        except ValueError:
            return
        if sid == self.studio_service_id:
            return

        # Reuse existing restart flow; deploy will happen once process is back.
        self.restart_service(sid, service_class=service_class)

        async def _do() -> None:
            # Give restart a moment to come back; readiness wait inside deploy handles most cases.
            await asyncio.sleep(0.3)
            await self._refresh_studio_runtime_async(compiled=compiled)
            await self._deploy_service_rungraph_async(sid, compiled=compiled)
            await self._set_service_active_async(sid, True)

        self._submit_async(_do(), context=f"submit restart_service_and_deploy failed serviceId={sid}")

    async def _set_managed_active_async(self, active: bool) -> None:
        requester = await self._ensure_requester()
        if requester is None:
            return
        service_ids = sorted({sid for sid in self._managed_service_ids if sid and sid != self.studio_service_id})
        if not service_ids:
            return

        for sid in service_ids:
            ok = await request_set_service_active(
                requester,
                service_id=sid,
                active=bool(active),
                attempts=3,
                timeout_s=0.5,
                retry_sleep_s=0.2,
            )
            if not ok:
                cmd = "activate" if bool(active) else "deactivate"
                self._emit_log_line(f"lifecycle {cmd} failed serviceId={sid}")

from __future__ import annotations

from qtpy import QtCore

from f8pysdk import F8ServiceSpec

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS as _CANVAS_SERVICE_CLASS_
from f8pystudio.studio_specs.identifiers import STUDIO_SERVICE_ID


class GraphServiceReclaimMixin:
    def clear_session(self, *args, **kwargs) -> None:
        """
        Clear the current canvas. Any removed service instances are reclaimed
        after a short debounce (so undo / immediate re-add won't kill processes).
        """
        nodes = list(self.all_nodes() or [])
        self._teardown_nodes(nodes)
        before: set[str] = set()
        for n in nodes:
            try:
                spec = n.spec
                if not isinstance(spec, F8ServiceSpec):
                    continue
                sid = str(n.id or "").strip()
                svc_class = str(spec.serviceClass or "")
                if sid and sid != STUDIO_SERVICE_ID and svc_class != _CANVAS_SERVICE_CLASS_:
                    before.add(sid)
            except (AttributeError, TypeError):
                continue
        super().clear_session(*args, **kwargs)
        for sid in sorted({s for s in before if s and s != STUDIO_SERVICE_ID}):
            self._schedule_service_reclaim(sid, delay_ms=3000)

    def _is_service_referenced(self, service_id: str) -> bool:
        """
        True if the serviceId is still referenced by the current canvas.
        """
        sid = str(service_id or "").strip()
        if not sid:
            return False
        try:
            n = self.get_node_by_id(sid)
            # Any service instance node with this id keeps the service alive.
            if n is not None and isinstance(n.spec, F8ServiceSpec):
                return True
        except (AttributeError, RuntimeError, TypeError):
            pass
        # If any operator still points at this svcId, keep the service alive.
        for n in self.all_nodes():
            if not self._is_operator_node(n):
                continue
            try:
                if str(n.svcId or "") == sid:
                    return True
            except (AttributeError, TypeError):
                continue
        return False

    def _schedule_service_reclaim(self, service_id: str, *, delay_ms: int = 3000) -> None:
        sid = str(service_id or "").strip()
        if not sid or sid == STUDIO_SERVICE_ID:
            return
        # Reset debounce timer.
        t = self._reclaim_timers.get(sid)
        if t is None:
            t = QtCore.QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(lambda _sid=sid: self._reclaim_service_if_unreferenced(_sid))  # type: ignore[attr-defined]
            self._reclaim_timers[sid] = t
        try:
            if t.isActive():
                t.stop()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            t.start(max(1, int(delay_ms)))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    def _reclaim_service_if_unreferenced(self, service_id: str) -> None:
        sid = str(service_id or "").strip()
        if not sid or sid == STUDIO_SERVICE_ID:
            return
        if self._is_service_referenced(sid):
            return
        bridge = self._service_bridge
        if bridge is None:
            return
        try:
            bridge.reclaim_service(sid)
        except (AttributeError, RuntimeError, TypeError):
            return


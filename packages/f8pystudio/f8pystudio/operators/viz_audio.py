from __future__ import annotations

import asyncio
import time
from typing import Any

from f8pysdk.specs import (
    F8OperatorSchemaVersion,
    F8OperatorSpec,
    F8RuntimeNode,
    F8StateAccess,
    F8StateSpec,
    boolean_schema,
    integer_schema,
    string_schema,
)
from f8pysdk.nats_naming import ensure_token
from f8pysdk.nodes import OperatorNode
from f8pysdk.registry import Registry
from f8pysdk.shm import audio_shm_name
from f8pysdk.zenoh_naming import zenoh_data_key

from f8pystudio.studio_specs.identifiers import SERVICE_CLASS
from f8pystudio.contracts.ui_commands import emit_ui_command
from .categories import PALETTE_CATEGORY_VIZ


OPERATOR_CLASS = "f8.viz.audio"
RENDERER_CLASS = "viz_audio"


def _default_audio_shm_name(service_id: str) -> str:
    s = str(service_id or "").strip()
    return audio_shm_name(s) if s else ""


def _default_audio_zenoh_key(service_id: str) -> str:
    s = str(service_id or "").strip()
    if not s:
        return ""
    try:
        return zenoh_data_key(s, node_id=s, port_id="audio")
    except ValueError:
        return ""


class VizAudioRuntimeNode(OperatorNode):
    """
    Studio-only visualization node: view Zenoh latest-audio or legacy SHM waveforms.

    This runtime node only pushes config to the UI layer; the Qt widget owns
    the selected latest-audio reader.
    """

    SPEC = F8OperatorSpec(
        schemaVersion=F8OperatorSchemaVersion.f8operator_1,
        serviceClass=SERVICE_CLASS,
        paletteCategory=PALETTE_CATEGORY_VIZ,
        operatorClass=OPERATOR_CLASS,
        version="0.0.1",
        label="Audio Viz",
        description="Display waveform from Zenoh latest-audio streams.",
        tags=["ui", "zenoh", "audio", "viewer", "waveform"],
        dataInPorts=[],
        dataOutPorts=[],
        rendererClass=RENDERER_CLASS,
        stateFields=[
            F8StateSpec(
                name="uiUpdate",
                label="UI Update",
                description="Pause/resume embedded viewer updates in the editor.",
                valueSchema=boolean_schema(default=True),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="serviceId",
                label="Service Id",
                description="Optional producer service id used to derive the default Zenoh audio key.",
                valueSchema=string_schema(default=""),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="shmName",
                label="Legacy Audio SHM",
                description="Legacy audio SHM mapping name used only when audioTransport=legacy_shm.",
                valueSchema=string_schema(default=""),
                access=F8StateAccess.rw,
                required=False,
                showOnNode=False,
            ),
            F8StateSpec(
                name="audioTransport",
                label="Audio Transport",
                description="Audio input transport backend. Zenoh uses audioKey; legacy_shm uses shmName.",
                valueSchema=string_schema(default="zenoh", enum=["zenoh", "legacy_shm"]),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="audioKey",
                label="Audio Key",
                description="Zenoh latest-audio key for input chunks.",
                valueSchema=string_schema(default=""),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=True,
            ),
            F8StateSpec(
                name="throttleMs",
                label="Refresh (ms)",
                description="UI refresh interval in milliseconds (0 = as fast as possible).",
                valueSchema=integer_schema(default=20, minimum=0, maximum=60000),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="historyMs",
                label="History (ms)",
                description="Waveform window length in milliseconds.",
                valueSchema=integer_schema(default=250, minimum=20, maximum=60000),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
            F8StateSpec(
                name="channel",
                label="Channel",
                description="Channel to display (0..N-1).",
                valueSchema=integer_schema(default=0, minimum=0, maximum=16),
                access=F8StateAccess.rw,
                required=True,
                showOnNode=False,
            ),
        ],
    )

    def __init__(self, *, node_id: str, node: F8RuntimeNode, initial_state: dict[str, Any] | None = None) -> None:
        super().__init__(
            node_id=ensure_token(node_id, label="node_id"),
            data_in_ports=[],
            data_out_ports=[],
            state_fields=[s.name for s in (node.stateFields or [])],
        )
        self._initial_state = dict(initial_state or {})
        self._config_loaded = False
        self._service_id = ""
        self._shm_name = ""
        self._audio_transport = "zenoh"
        self._audio_key = ""
        self._throttle_ms = 20
        self._history_ms = 250
        self._channel = 0
        self._pending_task: asyncio.Task[object] | None = None

    def attach(self, bus: Any) -> None:
        super().attach(bus)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._ensure_config_loaded(), name=f"pystudio:audioshm:init:{self.node_id}")
        except RuntimeError:
            pass

    async def close(self) -> None:
        try:
            t = self._pending_task
            self._pending_task = None
            if t is not None:
                t.cancel()
                await asyncio.gather(t, return_exceptions=True)
        except (RuntimeError, TypeError):
            pass
        emit_ui_command(self.node_id, "viz.audio.detach", {}, ts_ms=int(time.time() * 1000))

    async def on_state(self, field: str, value: Any, *, ts_ms: int | None = None) -> None:
        del value
        f = str(field or "").strip()
        if f not in ("serviceId", "shmName", "audioTransport", "audioKey", "throttleMs", "historyMs", "channel"):
            return
        await self._ensure_config_loaded()
        if f == "serviceId":
            self._service_id = str(await self._get_str_state("serviceId", default=self._service_id)).strip()
        elif f == "shmName":
            self._shm_name = str(await self._get_str_state("shmName", default=self._shm_name)).strip()
        elif f == "audioTransport":
            transport = str(await self._get_str_state("audioTransport", default=self._audio_transport)).strip().lower()
            self._audio_transport = transport if transport in ("legacy_shm", "zenoh") else "zenoh"
        elif f == "audioKey":
            self._audio_key = str(await self._get_str_state("audioKey", default=self._audio_key)).strip()
        elif f == "throttleMs":
            self._throttle_ms = await self._get_int_state("throttleMs", default=self._throttle_ms, minimum=0, maximum=60000)
        elif f == "historyMs":
            self._history_ms = await self._get_int_state("historyMs", default=self._history_ms, minimum=20, maximum=60000)
        elif f == "channel":
            self._channel = await self._get_int_state("channel", default=self._channel, minimum=0, maximum=16)
        await self._push_config(now_ms=int(ts_ms) if ts_ms is not None else int(time.time() * 1000))

    async def _ensure_config_loaded(self) -> None:
        if self._config_loaded:
            return
        self._service_id = str(await self._get_str_state("serviceId", default=str(self._initial_state.get("serviceId", "")))).strip()
        self._shm_name = str(await self._get_str_state("shmName", default=str(self._initial_state.get("shmName", "")))).strip()
        transport = str(
            await self._get_str_state("audioTransport", default=str(self._initial_state.get("audioTransport", "zenoh")))
        ).strip().lower()
        self._audio_transport = transport if transport in ("legacy_shm", "zenoh") else "zenoh"
        self._audio_key = str(
            await self._get_str_state("audioKey", default=str(self._initial_state.get("audioKey", "")))
        ).strip()
        self._throttle_ms = await self._get_int_state("throttleMs", default=20, minimum=0, maximum=60000)
        self._history_ms = await self._get_int_state("historyMs", default=250, minimum=20, maximum=60000)
        self._channel = await self._get_int_state("channel", default=0, minimum=0, maximum=16)
        self._config_loaded = True
        await self._push_config(now_ms=int(time.time() * 1000))

    async def _push_config(self, *, now_ms: int) -> None:
        if self._pending_task is not None and not self._pending_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pending_task = loop.create_task(self._push_config_async(now_ms), name=f"pystudio:audioshm:cfg:{self.node_id}")

    async def _push_config_async(self, now_ms: int) -> None:
        shm_name = str(self._shm_name or "").strip()
        if not shm_name and self._audio_transport == "legacy_shm":
            shm_name = str(self._audio_key or "").strip()
        if not shm_name and self._audio_transport == "legacy_shm":
            shm_name = _default_audio_shm_name(self._service_id)
        audio_key = str(self._audio_key or "").strip()
        if not audio_key and self._audio_transport == "zenoh":
            audio_key = _default_audio_zenoh_key(self._service_id)
        emit_ui_command(
            self.node_id,
            "viz.audio.set",
            {
                "shmName": shm_name,
                "serviceId": str(self._service_id or "").strip(),
                "audioTransport": str(self._audio_transport or "zenoh"),
                "audioKey": audio_key,
                "throttleMs": int(self._throttle_ms),
                "historyMs": int(self._history_ms),
                "channel": int(self._channel),
            },
            ts_ms=int(now_ms),
        )

    async def _get_int_state(self, name: str, *, default: int, minimum: int, maximum: int) -> int:
        v: Any = None
        try:
            v = await self.get_state_value(name)
        except Exception:
            v = None
        if v is None:
            v = self._initial_state.get(name)
        try:
            out = int(v) if v is not None else int(default)
        except Exception:
            out = int(default)
        if out < minimum:
            out = minimum
        if out > maximum:
            out = maximum
        return out

    async def _get_str_state(self, name: str, *, default: str) -> str:
        v: Any = None
        try:
            v = await self.get_state_value(name)
        except Exception:
            v = None
        if v is None:
            v = self._initial_state.get(name)
        try:
            s = str(v) if v is not None else str(default)
        except Exception:
            s = str(default)
        return s


def register_operator(registry: Registry) -> Registry:
    registry.register_operator(VizAudioRuntimeNode.SPEC, VizAudioRuntimeNode, overwrite=True)
    return registry

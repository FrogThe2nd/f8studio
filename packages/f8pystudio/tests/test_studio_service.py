from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

PKG_STUDIO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG_SDK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "f8pysdk"))
for p in (PKG_STUDIO, PKG_SDK):
    if p not in sys.path:
        sys.path.insert(0, p)

from f8pystudio.bridge.studio_service import PyStudioService, PyStudioServiceConfig  # noqa: E402


class _FakeServiceRuntime:
    instances: list["_FakeServiceRuntime"] = []

    def __init__(self, config, *, registry) -> None:
        self.config = config
        self.registry = registry
        self.bus = object()
        self.started = False
        self.stopped = False
        self.__class__.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class PyStudioServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_uses_callback_data_delivery_for_viz_nodes(self) -> None:
        _FakeServiceRuntime.instances.clear()
        service = PyStudioService(PyStudioServiceConfig(nats_url="nats://127.0.0.1:4222"))

        with (
            patch("f8pystudio.bridge.studio_service.ServiceRuntime", _FakeServiceRuntime),
            patch("f8pystudio.bridge.studio_service.register_operator", lambda registry: registry),
            patch("f8pystudio.bridge.studio_service.load_entrypoint_plugins", lambda: []),
        ):
            await service.start(on_ui_command=None)

        self.assertEqual(len(_FakeServiceRuntime.instances), 1)
        runtime = _FakeServiceRuntime.instances[0]
        self.assertTrue(runtime.started)
        self.assertEqual(runtime.config.bus.data_delivery, "callback")
        self.assertEqual(runtime.config.bus.cross_publish_policy, "routed")


if __name__ == "__main__":
    unittest.main()

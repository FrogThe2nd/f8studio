from __future__ import annotations

import inspect
import os
from multiprocessing.shared_memory import SharedMemory

_SUPPORTS_SHARED_MEMORY_TRACK = "track" in inspect.signature(SharedMemory).parameters


def _resource_tracker_unregister_shared_memory(shm: SharedMemory) -> None:
    if os.name != "posix":
        return
    try:
        from multiprocessing import resource_tracker  # type: ignore

        try:
            rt_name = str(shm._name)  # type: ignore[attr-defined]
        except AttributeError:
            rt_name = "/" + str(shm.name).lstrip("/")
        resource_tracker.unregister(rt_name, "shared_memory")
    except (ImportError, OSError, ValueError):
        return


def _open_shared_memory(*, name: str, create: bool, size: int = 0, track: bool = True) -> SharedMemory:
    if _SUPPORTS_SHARED_MEMORY_TRACK:
        return SharedMemory(name=name, create=create, size=int(size), track=track)

    shm = SharedMemory(name=name, create=create, size=int(size))
    if os.name == "posix" and not track:
        _resource_tracker_unregister_shared_memory(shm)
    return shm


def open_shared_memory_readonly(name: str) -> SharedMemory:
    """
    Attach to an existing shared memory block without ever unlinking it on process exit.

    On POSIX, Python's multiprocessing.resource_tracker may call shm_unlink() at shutdown
    if the SHM name is tracked, even when create=False. This helper unregisters the name
    to avoid breaking other processes still using the SHM.
    """
    return _open_shared_memory(name=name, create=False, track=False)


def open_shared_memory_create(name: str, size: int) -> SharedMemory:
    """
    Create a new shared memory block.

    Note: unlinking is a separate, explicit action (call shm.unlink()).
    """
    return _open_shared_memory(name=name, create=True, size=size)

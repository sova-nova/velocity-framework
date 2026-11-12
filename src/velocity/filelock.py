"""Exclusive file locks across processes, on Linux, macOS and Windows (added 2026-11-11 for
the Windows app): `fcntl.flock` where it exists, else `msvcrt.locking` on the file's first
byte. Used for the one-app-at-a-time lock and the one-OCR-at-a-time lock."""

import sys
import time
from typing import IO


class Locked(Exception):
    """The lock is held by another process (only when not waiting)."""


def lock(handle: IO, wait: bool = True) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if not wait:
                    raise Locked from None
                time.sleep(0.2)
    import fcntl

    try:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except BlockingIOError:
        raise Locked from None


def unlock(handle: IO) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle, fcntl.LOCK_UN)

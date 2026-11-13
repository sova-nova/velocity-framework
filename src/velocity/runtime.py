"""Whether velocity runs from its packaged app (the AppImage, decided 2026-11-11) or from a
Python install, and how it starts itself in a new process (the MCP server, Claude Code's
hooks, source jobs). Packaged, there is no `python -m velocity.cli`: the app's own binary
takes the same arguments.
"""

import os
import sys
from pathlib import Path


def packaged() -> bool:
    """Running from the PyInstaller bundle (inside the AppImage)."""
    return bool(getattr(sys, "frozen", False))


def self_command(*args: str) -> list[str]:
    """The command line that runs `velocity <args>` in a new process. On Windows the
    packaged app is windowed (velocity.exe, no console), so its subprocesses, which talk
    over stdio, run the console twin next to it, velocity-cli.exe."""
    if packaged():
        if sys.platform == "win32":
            return [str(Path(sys.executable).with_name("velocity-cli.exe")), *args]
        return [sys.executable, *args]
    return [sys.executable, "-m", "velocity.cli", *args]


def system_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for starting one of the system's own programs (the link opener).
    The packaged app's loader puts its bundled libraries first in LD_LIBRARY_PATH, and
    keeps the original in LD_LIBRARY_PATH_ORIG; a system program given the bundled Qt
    crashes (KDE's kde-open did, so links did nothing). Unpackaged, nothing changes."""
    env = dict(os.environ if environ is None else environ)
    if not packaged():
        return env
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original:
        env["LD_LIBRARY_PATH"] = original
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def no_window() -> int:
    """creationflags for a background subprocess: on Windows, no console window pops up."""
    import subprocess

    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

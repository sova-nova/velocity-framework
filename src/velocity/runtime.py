"""Whether velocity runs from its packaged app (the AppImage, decided 2026-11-11) or from a
Python install, and how it starts itself in a new process (the MCP server, Claude Code's
hooks, source jobs). Packaged, there is no `python -m velocity.cli`: the app's own binary
takes the same arguments.
"""

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


def no_window() -> int:
    """creationflags for a background subprocess: on Windows, no console window pops up."""
    import subprocess

    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

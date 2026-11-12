"""Whether velocity runs from its packaged app (the AppImage, decided 2026-11-11) or from a
Python install, and how it starts itself in a new process (the MCP server, Claude Code's
hooks, source jobs). Packaged, there is no `python -m velocity.cli`: the app's own binary
takes the same arguments.
"""

import sys


def packaged() -> bool:
    """Running from the PyInstaller bundle (inside the AppImage)."""
    return bool(getattr(sys, "frozen", False))


def self_command(*args: str) -> list[str]:
    """The command line that runs `velocity <args>` in a new process."""
    if packaged():
        return [sys.executable, *args]
    return [sys.executable, "-m", "velocity.cli", *args]

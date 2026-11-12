"""The packaged app's entry point (the AppImage, decided 2026-11-11): the same command line
as `velocity`, and with no arguments (a double-click, or the app menu) it opens the app."""

import sys

from velocity.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["app"]))

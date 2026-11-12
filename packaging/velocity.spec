# PyInstaller spec for velocity's packaged app (the AppImage, decided 2026-11-11).
# Built by packaging/build_appimage.sh; not meant to be run by hand.
#
# - velocity imports most of its modules inside functions, so every submodule is listed.
# - The OCR stack (marker-pdf, torch) is left out: the app reads text-layer PDFs, EPUB,
#   Markdown and HTML, and looks at pages, but can't OCR scanned or math-heavy PDFs.
# - Package metadata is kept where code reads it at run time (velocity's version for
#   Check for updates; keyring's backends are found through entry points).
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

hiddenimports = (
    collect_submodules("velocity")
    + collect_submodules("uvicorn")
    + collect_submodules("keyring.backends")
    + ["webview.platforms.qt"]
)
datas = (
    collect_data_files("velocity")
    + copy_metadata("velocity")
    + copy_metadata("keyring")
)
excludes = ["torch", "torchvision", "marker", "surya", "transformers", "tkinter"]

a = Analysis(
    ["velocity_app.py"],
    hiddenimports=hiddenimports,
    datas=datas,
    excludes=excludes,
)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    # Windows (2026-11-11): velocity.exe is the app, windowed (no console window);
    # velocity-cli.exe is the same program with a console, which velocity starts for its
    # own subprocesses (the tool server, hooks, source jobs) since those talk over stdio.
    app = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="velocity",
        console=False,
        icon="velocity.ico",
    )
    cli = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="velocity-cli",
        console=True,
        icon="velocity.ico",
    )
    coll = COLLECT(app, cli, a.binaries, a.datas, name="velocity")
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="velocity",
        console=True,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="velocity")

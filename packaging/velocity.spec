# PyInstaller spec for velocity's packaged app (the AppImage, decided 2026-11-11).
# Built by packaging/build_appimage.sh; not meant to be run by hand.
#
# - velocity imports most of its modules inside functions, so every submodule is listed.
# - The OCR stack (marker-pdf, torch) is left out: the app reads text-layer PDFs, EPUB,
#   Markdown and HTML, and looks at pages, but can't OCR scanned or math-heavy PDFs.
# - Package metadata is kept where code reads it at run time (velocity's version for
#   Check for updates; keyring's backends are found through entry points).
import os
import re
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
# Linux (found 2026-11-12, a crash before anything rendered): libraries that must match
# the user's system come from the system, not the bundle. A bundled C++ runtime from the
# build machine (Ubuntu 22.04) is older than the one the user's graphics drivers need, and
# a bundled libgbm looks for drivers where the build machine keeps them; either breaks Qt's
# web engine. So the C++ runtime, the graphics stack, X11/Wayland, fonts, D-Bus, sound and
# the desktop (GTK, GLib) libraries are left out; every Linux desktop has them. This is
# the usual AppImage practice.
LINUX_SYSTEM_LIBRARIES = re.compile(
    r"^lib("
    r"stdc\+\+|gcc_s"
    r"|GL|EGL|OpenGL|GLX|GLdispatch|gbm|drm|glapi|vulkan"
    r"|xcb|X11|Xext|Xrender|Xi|Xfixes|Xrandr|Xcursor|Xinerama|Xcomposite|Xdamage|Xtst"
    r"|Xau|Xdmcp|xkbcommon|wayland"
    r"|fontconfig|freetype|harfbuzz|fribidi|graphite2|pixman|png16|expat|uuid"
    r"|dbus-1|asound|systemd|cap|blkid|mount|selinux|pcre"
    r"|gtk-3|gdk-3|gdk_pixbuf|atk|atspi|cairo|pango|epoxy|thai|datrie|cups|avahi"
    r"|glib-2|gobject-2|gio-2|gmodule-2|gthread-2"
    r"|gnutls|nettle|hogweed|gmp|idn2|unistring|tasn1|p11-kit"
    r"|krb5|k5crypto|gssapi|com_err|keyutils|gcrypt|gpg-error|lz4|bsd|md|brotli"
    r"|jpeg|tiff|webp"
    r")[\w+.-]*\.so"
)
if sys.platform.startswith("linux"):
    a.binaries = [
        b for b in a.binaries if not LINUX_SYSTEM_LIBRARIES.match(os.path.basename(b[0]))
    ]

pyz = PYZ(a.pure)

# Python's UTF-8 mode, so text files are UTF-8 on Windows too (Windows' default is its
# old code page); velocity's own code names its encodings, this is the safety net.
utf8_mode = [("X utf8_mode=1", None, "OPTION")]

if sys.platform == "win32":
    # Windows (2026-11-11): velocity.exe is the app, windowed (no console window);
    # velocity-cli.exe is the same program with a console, which velocity starts for its
    # own subprocesses (the tool server, hooks, source jobs) since those talk over stdio.
    app = EXE(
        pyz,
        a.scripts,
        utf8_mode,
        exclude_binaries=True,
        name="velocity",
        console=False,
        icon="velocity.ico",
    )
    cli = EXE(
        pyz,
        a.scripts,
        utf8_mode,
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

"""The desktop app (decided 2026-11-11): velocity in its own window, for people who don't
use a terminal. `velocity app [subject]` starts velocity's web server on a free port and
shows the page in a native window (pywebview on Qt's Chromium engine; the optional `app`
dependency group). The page itself is the same as `velocity web`'s.

- One app at a time: a second launch brings the open window to the front and exits.
- Closing the window stops the server, which pauses any open chat (as stopping
  `velocity web` does); nothing is lost, so there is no prompt.
- The page's storage (remembered engine, model, tabs) lives in velocity's data folder, so
  it survives restarts.
- `install_launcher` adds velocity to the desktop's app menu (a .desktop entry and the
  icon), from Settings or `velocity install-launcher`.
"""

import fcntl
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from importlib.resources import files
from pathlib import Path

from velocity.config import data_dir
from velocity.runtime import self_command

APP_ID = "velocity"
TITLE = "velocity"
RAISE_PATH = "/__app/raise"
LOCK_NAME = "app.lock"


# -- one app at a time ---------------------------------------------------------------


class AlreadyOpen(Exception):
    pass


class InstanceLock:
    """An exclusive lock on <data>/velocity/app.lock, holding the running app's port and
    a token. A second app reads them to ask the first to show its window."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / LOCK_NAME
        self.token = secrets.token_urlsafe(16)
        self._file = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.close()
            raise AlreadyOpen from None
        self._file = f

    def publish(self, port: int) -> None:
        self._file.seek(0)
        self._file.truncate()
        self._file.write(json.dumps({"port": port, "token": self.token}))
        self._file.flush()

    def release(self) -> None:
        if self._file:
            self._file.close()
            self._file = None

    def ask_to_raise(self) -> bool:
        """From a second app: ask the running one to show its window."""
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
            request = urllib.request.Request(
                f"http://127.0.0.1:{info['port']}{RAISE_PATH}",
                method="POST",
                headers={"X-Velocity-App": info["token"]},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status == 204
        except (OSError, ValueError, KeyError):
            return False


class RaiseRoute:
    """Wraps the web app: POST /__app/raise with the lock's token shows the window."""

    def __init__(self, app, token: str, on_raise) -> None:
        self.app, self.token, self.on_raise = app, token, on_raise

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] == RAISE_PATH:
            headers = dict(scope.get("headers") or [])
            ok = scope["method"] == "POST" and secrets.compare_digest(
                headers.get(b"x-velocity-app", b"").decode(), self.token
            )
            if ok:
                self.on_raise()
            await send({"type": "http.response.start", "status": 204 if ok else 403})
            await send({"type": "http.response.body", "body": b""})
            return
        await self.app(scope, receive, send)


# -- the window ----------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def icon_svg() -> Path:
    return Path(str(files("velocity").joinpath("web_static/icon.svg")))


def icon_png() -> Path:
    return Path(str(files("velocity").joinpath("web_static/icon-256.png")))


def _qt_application() -> None:
    """Create Qt's application before pywebview does, with velocity's name and icon, so
    the desktop (KDE's taskbar, say) matches the window to the menu entry."""
    from qtpy.QtGui import QIcon
    from qtpy.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon(str(icon_png())))


def run_app(subject_slug: str | None = None) -> int:
    """Open velocity in its own window; return when the window is closed."""
    try:
        import webview
    except ImportError:
        print(
            "The desktop window needs the app packages: run `uv sync --group app` "
            "(or `uv sync`), then try again."
        )
        return 1
    import uvicorn

    from velocity.config import load_subject
    from velocity.setup import load_config_or_none
    from velocity.web import HOST, serve_app

    lock = InstanceLock()
    try:
        lock.acquire()
    except AlreadyOpen:
        if lock.ask_to_raise():
            return 0
        print("velocity is already open.")
        return 1

    try:
        config = load_config_or_none()
        if config and subject_slug:
            load_subject(config, subject_slug)  # fail early on an unknown subject
        port = free_port()
        window = None

        def show_window() -> None:
            if window is not None:
                window.restore()
                window.show()

        app = RaiseRoute(serve_app(config), lock.token, show_window)
        server = uvicorn.Server(
            uvicorn.Config(app, host=HOST, port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            if not thread.is_alive():
                print("velocity's server could not start.")
                return 1
            time.sleep(0.05)
        lock.publish(port)

        url = f"http://{HOST}:{port}/" + (
            f"?subject={subject_slug}" if subject_slug else ""
        )
        _qt_application()
        window = webview.create_window(
            TITLE, url, width=1280, height=860, min_size=(720, 560)
        )
        storage = data_dir() / "app-webview"
        storage.mkdir(parents=True, exist_ok=True)
        webview.start(
            gui="qt",
            private_mode=False,  # keep the page's remembered choices
            storage_path=str(storage),
            icon=str(icon_png()),
        )
        # The window is closed: stop the server, which pauses any open chat.
        server.should_exit = True
        thread.join(timeout=30)
        return 0
    finally:
        lock.release()


# -- the app menu --------------------------------------------------------------------


def launch_command() -> list[str]:
    """How the app menu should start velocity: the AppImage itself when running from
    one, else this install's `velocity` command."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return [appimage, "app"]
    script = Path(sys.executable).parent / "velocity"
    if script.exists():
        return [str(script), "app"]
    found = shutil.which("velocity")
    if found:
        return [found, "app"]
    return self_command("app")


def applications_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "applications"


def launcher_path() -> Path:
    return applications_dir() / f"{APP_ID}.desktop"


def _quote(arg: str) -> str:
    """An Exec argument, quoted as the desktop entry spec asks."""
    if not any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`"):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")
    return '"' + escaped.replace("$", "\\$") + '"'


def desktop_entry(command: list[str]) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=velocity\n"
        "GenericName=Tutor\n"
        "Comment=A tutor for any subject, with your own books and notes\n"
        f"Exec={' '.join(_quote(a) for a in command)}\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "Categories=Education;\n"
        f"StartupWMClass={APP_ID}\n"
    )


def install_launcher() -> Path:
    """Add velocity to the app menu: a .desktop entry and the icon, for this user only.
    Running it again updates them (after moving the AppImage, say)."""
    if sys.platform != "linux":
        raise OSError("Adding velocity to the app menu works on Linux only for now.")
    base = applications_dir().parent
    icon = base / "icons/hicolor/scalable/apps" / f"{APP_ID}.svg"
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon_svg(), icon)
    entry = launcher_path()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(desktop_entry(launch_command()), encoding="utf-8")
    entry.chmod(0o755)
    if shutil.which("update-desktop-database"):
        subprocess.run(
            ["update-desktop-database", str(entry.parent)],
            check=False,
            capture_output=True,
        )
    return entry


def launcher_installed() -> bool:
    return launcher_path().exists()

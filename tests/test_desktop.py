"""The desktop app (desktop.py): one app at a time, the raise route, and the app menu
entry. No window is opened here (CI doesn't install the `app` group)."""

import sys

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from velocity import desktop
from velocity.desktop import (
    RAISE_PATH,
    AlreadyOpen,
    InstanceLock,
    RaiseRoute,
    desktop_entry,
    install_launcher,
    launch_command,
    launcher_installed,
)


def test_only_one_app_at_a_time(tmp_path):
    first = InstanceLock(tmp_path / "app.lock")
    first.acquire()
    first.publish(41234)
    second = InstanceLock(tmp_path / "app.lock")
    with pytest.raises(AlreadyOpen):
        second.acquire()
    assert '"port": 41234' in (tmp_path / "app.json").read_text()
    first.release()
    second.acquire()  # free again once the first app is closed
    second.release()


def test_the_raise_route_needs_the_token():
    raised = []
    page = Starlette(routes=[Route("/", lambda r: PlainTextResponse("page"))])
    client = TestClient(RaiseRoute(page, "secret", lambda: raised.append(1)))
    assert (
        client.post(RAISE_PATH, headers={"X-Velocity-App": "wrong"}).status_code == 403
    )
    assert (
        client.get(RAISE_PATH, headers={"X-Velocity-App": "secret"}).status_code == 403
    )
    assert raised == []
    assert (
        client.post(RAISE_PATH, headers={"X-Velocity-App": "secret"}).status_code == 204
    )
    assert raised == [1]
    assert client.get("/").text == "page"  # everything else is the web app


def test_a_second_launch_with_a_stale_lock_file_says_so(tmp_path):
    lock = InstanceLock(tmp_path / "app.lock")
    (tmp_path / "app.json").write_text('{"port": 9, "token": "x"}')
    assert lock.ask_to_raise() is False  # nothing listens there


def test_launch_command_prefers_the_appimage(monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/home/me/Apps/velocity.AppImage")
    assert launch_command() == ["/home/me/Apps/velocity.AppImage", "app"]
    monkeypatch.delenv("APPIMAGE")
    command = launch_command()
    assert command[-1] == "app" and "velocity" in " ".join(command)


def test_desktop_entry_quotes_paths_with_spaces():
    entry = desktop_entry(["/home/me/My Apps/velocity", "app"])
    assert 'Exec="/home/me/My Apps/velocity" app\n' in entry
    assert "Icon=velocity\n" in entry and "Terminal=false\n" in entry
    assert "StartupWMClass=velocity\n" in entry


@pytest.mark.skipif(sys.platform != "linux", reason="the app menu entry is Linux only")
def test_install_launcher_writes_the_entry_and_icon(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)  # no menu refresh
    assert not launcher_installed()
    entry = install_launcher()
    assert entry == tmp_path / "share/applications/velocity.desktop"
    assert "Exec=" in entry.read_text() and launcher_installed()
    icon = tmp_path / "share/icons/hicolor/scalable/apps/velocity.svg"
    assert icon.read_text().startswith("<svg")
    assert (tmp_path / "share/icons/hicolor/256x256/apps/velocity.png").exists()
    # By its full path: a running desktop's icon cache doesn't see a new icon by name.
    assert f"Icon={icon.as_posix()}\n" in entry.read_text()


def test_settings_add_the_launcher(tmp_path, monkeypatch):
    from velocity.setup import settings_routes

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    client = TestClient(Starlette(routes=settings_routes(lambda: None, None)))
    before = client.get("/api/settings").json()["launcher"]
    assert before == {"supported": sys.platform == "linux", "installed": False}
    if sys.platform == "linux":
        added = client.post("/api/settings/launcher").json()
        assert added["installed"] and added["path"].endswith("velocity.desktop")
        assert client.get("/api/settings").json()["launcher"]["installed"]


def test_without_the_app_packages_it_says_how_to_get_them(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "webview", None)  # import webview fails
    assert desktop.run_app() == 1
    assert "uv sync --group app" in capsys.readouterr().out

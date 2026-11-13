"""The packaged app (the AppImage, decided 2026-11-11): starting velocity's own commands,
the OCR note, and Check for updates (GitHub on a mock transport)."""

import asyncio
import sys

import httpx

from velocity import runtime, setup
from velocity.setup import latest_release, version_tuple
from velocity.workspace import build_mcp_config


def test_self_command_from_python_and_packaged(monkeypatch):
    assert runtime.self_command("mcp", "--subject", "x")[1:3] == ["-m", "velocity.cli"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert runtime.packaged()
    assert runtime.self_command("mcp", "--subject", "x") == [
        sys.executable,
        "mcp",
        "--subject",
        "x",
    ]


def test_on_windows_subprocesses_run_the_console_twin(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(Path("app") / "velocity.exe"))
    assert runtime.self_command("mcp") == [str(Path("app") / "velocity-cli.exe"), "mcp"]


def test_claude_codes_tool_server_runs_the_packaged_binary(monkeypatch):
    from types import SimpleNamespace as NS

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    server = build_mcp_config(NS(slug="calculus"))["mcpServers"]["velocity"]
    assert server == {
        "command": sys.executable,
        "args": ["mcp", "--subject", "calculus"],
    }


def test_the_packaged_app_explains_its_missing_ocr(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    ocr = next(c for c in setup.checks(None) if c["name"] == "Reading PDFs (OCR)")
    assert "downloaded app doesn't include OCR" in ocr["fix"]


def test_version_order():
    assert version_tuple("v0.10.0") > version_tuple("0.9.3")
    assert version_tuple("1.0") < version_tuple("1.0.1")


def github(status, body=None):
    def handler(request):
        assert request.url.path.endswith("/releases/latest")
        return httpx.Response(status, json=body or {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_check_for_updates(monkeypatch):
    monkeypatch.setattr("velocity.__version__", "0.1.0")
    newer = asyncio.run(
        latest_release(github(200, {"tag_name": "v0.2.0", "html_url": "https://x/r"}))
    )
    assert (
        newer["newer"] and newer["latest"] == "0.2.0" and newer["url"] == "https://x/r"
    )
    same = asyncio.run(latest_release(github(200, {"tag_name": "v0.1.0"})))
    assert not same["newer"] and same["current"] == "0.1.0"
    none = asyncio.run(latest_release(github(404)))
    assert "No release" in none["error"]

    def refuse(request):
        raise httpx.ConnectError("offline", request=request)

    offline = asyncio.run(
        latest_release(httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    )
    assert "Couldn't reach GitHub" in offline["error"]


def test_system_programs_get_the_systems_libraries(monkeypatch):
    bundled = {
        "LD_LIBRARY_PATH": "/app/_internal",
        "LD_LIBRARY_PATH_ORIG": "/opt/lib",
        "A": "1",
    }
    assert runtime.system_env(bundled) == bundled  # unpackaged: nothing changes
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert runtime.system_env(bundled) == {"LD_LIBRARY_PATH": "/opt/lib", "A": "1"}
    # No original: the bundled path is dropped, not kept.
    assert runtime.system_env({"LD_LIBRARY_PATH": "/app/_internal", "A": "1"}) == {
        "A": "1"
    }

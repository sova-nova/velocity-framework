"""First-run setup and settings (M10): writing config.toml, the setup screen, and the app
switching to the full app (or another notes folder) without a restart."""

import pytest
from starlette.testclient import TestClient

from velocity.config import (
    ConfigError,
    config_path,
    create_subject,
    load_config,
    save_settings,
)
from velocity.setup import vault_suggestions
from velocity.web import serve_app

ORIGIN = {"Origin": "http://127.0.0.1:8765"}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A home folder with one Obsidian vault, so suggestions never scan the real one."""
    home = tmp_path / "home"
    (home / "Documents/Brain/.obsidian").mkdir(parents=True)
    (home / ".hidden/Vault/.obsidian").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() on Windows
    return home


# -- config.toml -------------------------------------------------------------------------


def test_first_save_creates_a_commented_config(home):
    with pytest.raises(ConfigError, match="notes folder first"):
        save_settings({"api_effort": "high"})
    with pytest.raises(ConfigError, match="not a folder"):
        save_settings({"vault_path": str(home / "Nope")})
    config = save_settings({"vault_path": str(home / "Notes")}, create_vault=True)
    assert config.vault_path == (home / "Notes").resolve()
    text = config_path().read_text()
    assert "# Your notes folder" in text and "llama_cpp_binary" in text


def test_settings_change_one_line_and_can_be_cleared(home, tmp_path):
    save_settings({"vault_path": str(home / "Documents/Brain")})
    llama = tmp_path / "llama-server"
    llama.write_text("#!/bin/sh\n")
    config = save_settings(
        {"llama_cpp_binary": str(llama), "api_model": "sonnet", "api_effort": "high"}
    )
    assert config.llama_cpp_binary == llama
    assert (config.api_model, config.api_effort) == ("sonnet", "high")
    config = save_settings({"llama_cpp_binary": "", "api_effort": ""})
    assert config.llama_cpp_binary is None and config.api_effort is None
    assert config.api_model == "sonnet"
    assert "# Your notes folder" in config_path().read_text()  # comments kept


def test_settings_are_checked_before_writing(home):
    save_settings({"vault_path": str(home / "Documents/Brain")})
    before = config_path().read_text()
    for bad in (
        {"api_model": "gpt-5"},
        {"api_effort": "turbo"},
        {"llama_cpp_binary": "/no/such/program"},
        {"vault_path": "relative/path"},
    ):
        with pytest.raises(ConfigError):
            save_settings(bad)
    assert config_path().read_text() == before


def test_vault_suggestions_find_obsidian_vaults_but_not_hidden_ones(home):
    assert vault_suggestions() == [str(home / "Documents/Brain")]


# -- the page ----------------------------------------------------------------------------


def test_setup_screen_then_the_full_app_without_a_restart(home):
    with TestClient(serve_app(None), base_url="http://127.0.0.1:8765") as client:
        settings = client.get("/api/settings").json()
        assert settings["configured"] is False
        assert settings["suggestions"] == [str(home / "Documents/Brain")]
        assert {c["name"] for c in settings["checks"]} == {
            "Claude Code",
            "API key",
            "Reading PDFs (OCR)",
        }
        assert client.get("/api/subjects").status_code == 404  # not set up yet

        saved = client.post(
            "/api/settings",
            json={"vault_path": str(home / "Documents/Brain")},
            headers=ORIGIN,
        )
        assert saved.status_code == 200 and saved.json()["configured"] is True
        assert client.get("/api/subjects").json() == []  # the full app now


def test_changing_the_notes_folder_shows_its_subjects(home):
    save_settings({"vault_path": str(home / "Documents/Brain")})
    other = home / "Other"
    other.mkdir()
    create_subject(save_settings({"vault_path": str(other)}), "Physics")
    save_settings({"vault_path": str(home / "Documents/Brain")})
    with TestClient(
        serve_app(load_config()), base_url="http://127.0.0.1:8765"
    ) as client:
        assert client.get("/api/subjects").json() == []
        saved = client.post(
            "/api/settings", json={"vault_path": str(other)}, headers=ORIGIN
        )
        assert saved.json()["reload"] is True
        assert [s["slug"] for s in client.get("/api/subjects").json()] == ["physics"]
        same = client.post("/api/settings", json={"api_effort": "low"}, headers=ORIGIN)
        assert same.json()["reload"] is False


def test_settings_wait_until_no_chat_is_open(home):
    save_settings({"vault_path": str(home / "Documents/Brain")})
    app = serve_app(load_config())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        app.inner.state.chats.chats["calculus"] = object()  # a chat is open
        refused = client.post(
            "/api/settings", json={"api_effort": "low"}, headers=ORIGIN
        )
        assert refused.status_code == 409 and "open chat" in refused.json()["error"]
        app.inner.state.chats.chats.clear()


# -- colors (decided 2026-11-11) ---------------------------------------------------------


def test_the_theme_is_saved_at_once_and_drawn_from_the_start(home):
    save_settings({"vault_path": str(home / "Documents/Brain")})
    app = serve_app(load_config())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/settings").json()["theme"] == "system"
        assert 'data-theme="' not in client.get("/").text.split(">", 2)[1]

        # Allowed while a chat is open: it rebuilds nothing.
        app.inner.state.chats.chats["calculus"] = object()
        saved = client.post(
            "/api/settings/theme", json={"theme": "dark"}, headers=ORIGIN
        )
        app.inner.state.chats.chats.clear()
        assert saved.status_code == 200 and saved.json() == {"theme": "dark"}
        assert 'theme = "dark"' in config_path().read_text()
        assert '<html lang="en" data-theme="dark">' in client.get("/").text
        assert client.get("/api/settings").json()["theme"] == "dark"

        bad = client.post("/api/settings/theme", json={"theme": "pink"}, headers=ORIGIN)
        assert bad.status_code == 400
        client.post("/api/settings/theme", json={"theme": "system"}, headers=ORIGIN)
        assert "theme" not in config_path().read_text().split("#", 1)[0]
        assert "data-theme" not in client.get("/").text.split("<head>", 1)[0]
        assert load_config().vault_path == (home / "Documents/Brain").resolve()


def test_no_theme_before_setup(home):
    with TestClient(serve_app(None), base_url="http://127.0.0.1:8765") as client:
        refused = client.post(
            "/api/settings/theme", json={"theme": "light"}, headers=ORIGIN
        )
        assert (
            refused.status_code == 400
            and "notes folder first" in refused.json()["error"]
        )
        assert client.get("/").status_code == 200

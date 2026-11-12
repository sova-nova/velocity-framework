"""API keys in the system keyring (keys.py, decided 2026-11-11). Every test runs on an
in-memory keyring (tests/conftest.py), never the learner's real one."""

import json

import keyring
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from velocity import keys, openai_engine
from velocity.api_engine import api_available
from velocity.config import Config
from velocity.setup import settings_routes

SECRET = "sk-ant-test-0123456789"


def test_a_saved_key_is_used_and_the_environment_wins(monkeypatch):
    assert keys.anthropic_key() is None and not api_available()
    keys.save_key(keys.ANTHROPIC, f"  {SECRET}\n")
    assert keys.anthropic_key() == SECRET and api_available()
    assert keys.status(keys.ANTHROPIC, keys.ANTHROPIC_ENV).source == "keyring"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert keys.anthropic_key() == "from-env"
    status = keys.status(keys.ANTHROPIC, keys.ANTHROPIC_ENV)
    assert (status.source, status.saved) == ("env", True)
    keys.remove_key(keys.ANTHROPIC)
    keys.remove_key(keys.ANTHROPIC)  # removing nothing is fine
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert keys.anthropic_key() is None


def test_bad_saves_are_refused():
    with pytest.raises(keys.KeyStoreError, match="Paste"):
        keys.save_key(keys.ANTHROPIC, "  ")
    with pytest.raises(keys.KeyStoreError, match="Unknown"):
        keys.save_key("github", SECRET)


def test_without_a_keyring_it_says_so(monkeypatch):
    from keyring.backends import fail

    keyring.set_keyring(fail.Keyring())
    assert not keys.keyring_available()
    assert keys.anthropic_key() is None  # no crash
    with pytest.raises(keys.KeyStoreError, match="environment variable"):
        keys.save_key(keys.ANTHROPIC, SECRET)


def test_a_free_servers_key_is_only_sent_to_that_server(tmp_path):
    keys.save_key(keys.free_key_name("https://openrouter.ai/api/v1"), "or-key")
    openrouter = Config(
        tmp_path,
        openai_base_url="https://openrouter.ai/api/v1",
        openai_model="m:free",
        openai_api_key_env="OPENROUTER_API_KEY",
    )
    assert openai_engine.unavailable_reason(openrouter) is None
    assert openai_engine.api_key(openrouter) == "or-key"
    elsewhere = Config(
        tmp_path, openai_base_url="https://example.com/v1", openai_model="m"
    )
    assert openai_engine.api_key(elsewhere) == openai_engine.NOT_NEEDED_KEY
    local = Config(
        tmp_path, openai_base_url="http://localhost:11434/v1", openai_model="m"
    )
    assert openai_engine.api_key(local) == openai_engine.NOT_NEEDED_KEY


def test_settings_save_and_remove_keys_without_ever_sending_them_back(
    tmp_path, memory_keyring
):
    config = Config(tmp_path, openai_base_url="https://openrouter.ai/api/v1")
    client = TestClient(Starlette(routes=settings_routes(lambda: config, None)))
    before = client.get("/api/settings").json()
    assert before["keys"]["anthropic"]["source"] is None
    assert before["keys"]["openai"]["host"] == "openrouter.ai"

    saved = client.post("/api/settings/key", json={"which": "anthropic", "key": SECRET})
    assert saved.status_code == 200 and saved.json()["anthropic"]["source"] == "keyring"
    assert SECRET not in saved.text
    assert SECRET not in json.dumps(client.get("/api/settings").json())
    assert memory_keyring.passwords[("velocity", "anthropic")] == SECRET

    free = client.post(
        "/api/settings/key",
        json={
            "which": "openai",
            "key": "or-key",
            "base_url": "https://openrouter.ai/api/v1",
        },
    )
    assert free.json()["openai"] == {
        "source": "keyring",
        "env_var": None,
        "saved": True,
        "host": "openrouter.ai",
    }
    other = client.get("/api/settings/keys?base_url=http://localhost:11434/v1").json()
    assert other["openai"]["saved"] is False and other["openai"]["host"] == "localhost"

    no_server = client.post("/api/settings/key", json={"which": "openai", "key": "k"})
    assert (
        no_server.status_code == 400 and "server address" in no_server.json()["error"]
    )

    removed = client.post(
        "/api/settings/key", json={"which": "anthropic", "remove": True}
    )
    assert removed.json()["anthropic"]["source"] is None
    assert ("velocity", "anthropic") not in memory_keyring.passwords


def test_the_api_engine_check_sees_a_saved_key(tmp_path):
    from velocity.setup import checks

    row = next(c for c in checks(None) if c["name"] == "API key")
    assert not row["ok"] and "Settings" in row["fix"]
    keys.save_key(keys.ANTHROPIC, SECRET)
    row = next(c for c in checks(None) if c["name"] == "API key")
    assert row["ok"] and row["detail"] == "saved in your system keyring"

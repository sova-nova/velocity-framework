"""Managing subjects from the web page (M10): create, settings, archive, recover, delete."""

import pytest
from starlette.testclient import TestClient

from velocity.config import (
    Config,
    ConfigError,
    add_source,
    create_subject,
    delete_subject,
    list_subjects,
    load_subject,
    set_archived,
    set_note_folders,
    set_persona,
    set_subject_name,
)
from velocity.session import record_dir, set_active
from velocity.web import build_app

ORIGIN = {"Origin": "http://127.0.0.1:8765"}


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.fixture
def config(tmp_path) -> Config:
    vault = tmp_path / "vault"
    (vault / "Math/Calculus").mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    config = Config(vault.resolve())
    create_subject(config, "Calculus")
    return config


@pytest.fixture
def client(config) -> TestClient:
    return TestClient(build_app(config), base_url="http://127.0.0.1:8765")


# -- editing subject.toml ----------------------------------------------------------------


def test_settings_change_one_line_and_keep_the_rest(config, tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# Limits\n")
    add_source(config, "calculus", book, "Notes")
    toml = config.tutor_dir / "calculus/subject.toml"

    set_subject_name(config, "calculus", 'Calculus "I"')
    set_persona(config, "calculus", "Be brief.\nUse SI units.")
    set_note_folders(config, "calculus", ["Math/Calculus"])
    subject = load_subject(config, "calculus")
    assert subject.name == 'Calculus "I"'
    assert subject.persona.strip() == "Be brief.\nUse SI units."
    assert subject.note_folders == (config.vault_path / "Math/Calculus",)
    assert subject.sources[0].name == "Notes"  # the [[sources]] table is untouched
    text = toml.read_text()
    assert "# Extra instructions for the tutor" in text  # comments kept
    assert "Be brief.\nUse SI units." in text  # readable when opened by hand

    set_persona(config, "calculus", "")
    set_note_folders(config, "calculus", [])
    subject = load_subject(config, "calculus")
    assert subject.persona.strip() == "" and subject.note_folders == ()


def test_persona_with_quotes_reads_back_exactly(config):
    tricky = "Say ''' and \"\"\" and \\n literally."
    assert set_persona(config, "calculus", tricky).persona == tricky


def test_settings_are_validated(config):
    with pytest.raises(ConfigError, match="empty"):
        set_subject_name(config, "calculus", "  ")
    with pytest.raises(ConfigError, match="not a folder"):
        set_note_folders(config, "calculus", ["Nope"])
    with pytest.raises(ConfigError, match="outside the vault"):
        set_note_folders(config, "calculus", ["../elsewhere"])
    with pytest.raises(ConfigError, match="belongs to the tutor"):
        set_note_folders(config, "calculus", ["Tutor"])


def test_archive_recover_and_delete(config):
    record_dir("calculus").mkdir(parents=True)
    (record_dir("calculus") / "2026-11-09-1.jsonl").write_text("{}\n")
    with pytest.raises(ConfigError, match="archive it first"):
        delete_subject(config, "calculus")

    assert set_archived(config, "calculus", True).archived
    assert "calculus" in list_subjects(config)  # listed, marked archived
    assert not set_archived(config, "calculus", False).archived
    assert "archived" not in (config.tutor_dir / "calculus/subject.toml").read_text()

    set_archived(config, "calculus", True)
    delete_subject(config, "calculus")
    assert not (config.tutor_dir / "calculus").exists()
    assert not record_dir("calculus").exists()
    assert list_subjects(config) == []


# -- the web endpoints -------------------------------------------------------------------


def test_create_subject_from_the_page(client):
    created = client.post(
        "/api/subjects", json={"name": "AI Engineering"}, headers=ORIGIN
    )
    assert created.json() == {"slug": "ai-engineering", "name": "AI Engineering"}
    again = client.post(
        "/api/subjects", json={"name": "AI engineering"}, headers=ORIGIN
    )
    assert again.status_code == 409
    slugs = [s["slug"] for s in client.get("/api/subjects").json()]
    assert slugs == ["ai-engineering", "calculus"]


def test_each_subject_has_a_color(client, config):
    from velocity.config import SUBJECT_COLORS, _set_top_level, subject_toml

    first = client.get("/api/subjects").json()[0]["color"]
    assert first in SUBJECT_COLORS
    assert client.get("/api/subjects").json()[0]["color"] == first  # stable
    _set_top_level(subject_toml(config, "calculus"), "color", '"#123abc"')
    assert client.get("/api/subjects").json()[0]["color"] == "#123abc"
    _set_top_level(subject_toml(config, "calculus"), "color", '"red"')  # not #rrggbb
    assert client.get("/api/subjects").json()[0]["color"] == first


def test_settings_from_the_page(client):
    url = "/api/subjects/calculus/settings"
    assert client.get(url).json()["note_folders"] == []
    saved = client.post(
        url,
        json={
            "name": "Calc",
            "persona": "Be brief.",
            "note_folders": ["Math/Calculus"],
        },
        headers=ORIGIN,
    ).json()
    assert saved["name"] == "Calc" and saved["persona"] == "Be brief."
    assert saved["note_folders"] == ["Math/Calculus"]
    bad = client.post(url, json={"note_folders": ["Nope"]}, headers=ORIGIN)
    assert bad.status_code == 400 and "not a folder" in bad.json()["error"]


def test_vault_folders_leave_out_hidden_and_tutor_folders(client):
    assert client.get("/api/vault/folders").json() == ["Math", "Math/Calculus"]


def test_archive_and_delete_from_the_page(client, config):
    base = "/api/subjects/calculus"
    set_active("calculus", "2026-11-09-1")  # a session is open
    assert client.post(f"{base}/archive", json={}, headers=ORIGIN).status_code == 409
    set_active("calculus", None)

    archived = client.post(f"{base}/archive", json={"archived": True}, headers=ORIGIN)
    assert archived.json()["archived"] is True
    assert client.get("/api/subjects").json()[0]["archived"] is True
    wrong = client.post(f"{base}/delete", json={"confirm": "calculus"}, headers=ORIGIN)
    assert wrong.status_code == 400  # the name, as shown: "Calculus"
    deleted = client.post(
        f"{base}/delete", json={"confirm": "Calculus"}, headers=ORIGIN
    )
    assert deleted.json() == {"deleted": "calculus"}
    assert client.get("/api/subjects").json() == []


def test_recover_and_refuse_deleting_an_active_subject(client):
    base = "/api/subjects/calculus"
    refused = client.post(
        f"{base}/delete", json={"confirm": "Calculus"}, headers=ORIGIN
    )
    assert refused.status_code == 400 and "archive it first" in refused.json()["error"]
    client.post(f"{base}/archive", json={"archived": True}, headers=ORIGIN)
    recovered = client.post(f"{base}/archive", json={"archived": False}, headers=ORIGIN)
    assert recovered.json()["archived"] is False

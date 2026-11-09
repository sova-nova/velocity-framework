"""The sources library (M10): one list of books across subjects, adding from it, and
removing and renaming sources."""

import pytest
from starlette.testclient import TestClient

from velocity import cli
from velocity.config import (
    Config,
    ConfigError,
    add_source,
    create_subject,
    load_subject,
    remove_source,
    rename_source,
)
from velocity.library import entry_id, library
from velocity.uploads import library_dir
from velocity.web import build_app

ORIGIN = {"Origin": "http://127.0.0.1:8765"}


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.fixture
def config(tmp_path) -> Config:
    (tmp_path / "vault").mkdir()
    config = Config((tmp_path / "vault").resolve())
    create_subject(config, "Calculus")
    create_subject(config, "AI Engineering")
    return config


@pytest.fixture
def books(tmp_path, config):
    stewart = tmp_path / "stewart.md"
    stewart.write_text("# Limits\n")
    notes = tmp_path / "notes.md"
    notes.write_text("# Agents\n")
    add_source(config, "calculus", stewart, "Stewart")
    add_source(config, "calculus", notes, "My notes")
    library_dir().mkdir(parents=True, exist_ok=True)
    (library_dir() / "uploaded.md").write_text("# Evals\n")
    (library_dir() / "half.md.part").write_text("# unfinished")
    return stewart, notes


@pytest.fixture
def client(config) -> TestClient:
    return TestClient(build_app(config), base_url="http://127.0.0.1:8765")


# -- the library ---------------------------------------------------------------------------


def test_library_lists_each_file_once_with_its_uses(config, books):
    _stewart, notes = books
    add_source(config, "ai-engineering", notes, "Notes")
    entries = {e.path.name: e for e in library(config)}
    assert set(entries) == {"stewart.md", "notes.md", "uploaded.md"}  # no .part
    shared = entries["notes.md"]
    assert [(u.subject_slug, u.source_name) for u in shared.uses] == [
        ("ai-engineering", "Notes"),
        ("calculus", "My notes"),
    ]
    assert shared.id == entry_id(notes)
    assert entries["uploaded.md"].uses == []
    assert entries["uploaded.md"].suggested_name == "uploaded"


def test_add_from_the_library_uses_the_same_file(client, config, books):
    stewart, _ = books
    lib = client.get("/api/library").json()
    entry = next(e for e in lib if e["filename"] == "stewart.md")
    assert entry["suggested_name"] == "Stewart"
    assert entry["uses"][0]["subject_name"] == "Calculus"

    added = client.post(
        "/api/subjects/ai-engineering/sources/library",
        json={"id": entry["id"], "name": "Stewart"},
        headers=ORIGIN,
    )
    assert added.status_code == 200, added.text
    assert added.json()["sections"] == 1
    assert load_subject(config, "ai-engineering").sources[0].path == stewart.resolve()

    again = client.post(
        "/api/subjects/ai-engineering/sources/library",
        json={"id": entry["id"], "name": "Again"},
        headers=ORIGIN,
    )
    assert again.status_code == 400 and "already a source" in again.json()["error"]


def test_only_library_entries_can_be_added(client, books, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("# private\n")
    response = client.post(
        "/api/subjects/ai-engineering/sources/library",
        json={"id": entry_id(secret), "name": "Secret"},
        headers=ORIGIN,
    )
    assert response.status_code == 400
    assert "no longer in the library" in response.json()["error"]


# -- removing and renaming -----------------------------------------------------------------


def test_remove_a_source_keeps_the_other_and_the_file(config, books):
    stewart, _notes = books
    toml = config.tutor_dir / "calculus/subject.toml"
    remove_source(config, "calculus", "Stewart")
    assert [s.name for s in load_subject(config, "calculus").sources] == ["My notes"]
    assert stewart.exists()
    assert "# Subject settings for velocity" in toml.read_text()  # comments kept
    remove_source(config, "calculus", "My notes")
    assert load_subject(config, "calculus").sources == ()
    with pytest.raises(ConfigError, match="no source named"):
        remove_source(config, "calculus", "Stewart")


def test_rename_a_source(config, books):
    rename_source(config, "calculus", "My notes", "Notes")
    names = [s.name for s in load_subject(config, "calculus").sources]
    assert names == ["Stewart", "Notes"]
    with pytest.raises(ConfigError, match="already has a source named"):
        rename_source(config, "calculus", "Notes", "Stewart")
    with pytest.raises(ConfigError, match="empty"):
        rename_source(config, "calculus", "Notes", " ")


def test_rename_a_source_without_a_name_line(config, tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# A\n")
    toml = config.tutor_dir / "calculus/subject.toml"
    toml.write_text(toml.read_text() + f'\n[[sources]]\npath = "{book}"\n')
    rename_source(config, "calculus", "book", "Book")  # the file name was its name
    assert load_subject(config, "calculus").sources[0].name == "Book"


def test_remove_and_rename_from_the_page(client, config, books):
    base = "/api/subjects/calculus/sources"
    renamed = client.post(
        f"{base}/My%20notes/rename", json={"name": "Notes"}, headers=ORIGIN
    )
    assert renamed.json() == {"name": "Notes"}
    settings = client.get("/api/subjects/calculus/settings").json()
    assert [s["name"] for s in settings["sources"]] == ["Stewart", "Notes"]
    assert settings["sources"][0] == {
        "name": "Stewart",
        "kind": "text",
        "filename": "stewart.md",
        "missing": False,
    }
    assert client.post(f"{base}/Stewart/remove", headers=ORIGIN).json() == {
        "removed": "Stewart"
    }
    assert client.post(f"{base}/Stewart/remove", headers=ORIGIN).status_code == 400
    # Still in the library, ready for another subject.
    assert "stewart.md" in [e["filename"] for e in client.get("/api/library").json()]


def test_cli_remove_and_rename(config, books, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: config)
    assert cli.main(["source", "rename", "calculus", "Stewart", "Stew"]) == 0
    assert "Earlier session logs keep citing the old name" in capsys.readouterr().out
    assert cli.main(["source", "remove", "calculus", "Stew"]) == 0
    assert [s.name for s in load_subject(config, "calculus").sources] == ["My notes"]


def test_a_deleted_subjects_books_stay_in_the_library(config, books):
    from velocity.config import delete_subject, set_archived

    set_archived(config, "calculus", True)
    delete_subject(config, "calculus")
    names = sorted(e.path.name for e in library(config))
    assert names == ["notes.md", "stewart.md", "uploaded.md"]
    assert all(not e.uses for e in library(config))

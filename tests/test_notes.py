import os
from pathlib import Path

import pytest

from velocity.cli import main
from velocity.config import (
    ConfigError,
    add_note_folder,
    create_subject,
    load_config,
    load_subject,
)
from velocity.notes import MAX_NOTE_CHARS, NotesError, is_private, list_notes, read_note
from velocity.tools import ToolError, TutorTools


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    files = {
        "Math/Calculus/limits.md": "# Limits\n\nA limit is where f goes.",
        "Math/Calculus/deep/epsilon.md": "Epsilon-delta notes",
        "Math/Calculus/diary.md": "---\nprivate: true\n---\nSecret thoughts",
        "Math/Calculus/quoted.md": "---\ntags: [x]\nprivate: 'yes'\n---\nAlso secret",
        "Math/Calculus/.trash/old.md": "deleted note",
        "Math/Calculus/image.png": "not markdown",
        "Math/Algebra/groups.md": "Groups",
        "Personal/journal.md": "Outside the note folders",
    }
    for rel, text in files.items():
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text(text)
    (v / "Math/Calculus/escape.md").symlink_to(v / "Personal/journal.md")
    return v


@pytest.fixture
def config(tmp_path, vault, monkeypatch):
    (tmp_path / "cfg/velocity").mkdir(parents=True)
    (tmp_path / "cfg/velocity/config.toml").write_text(f'vault_path = "{vault}"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    cfg = load_config()
    create_subject(cfg, "Calculus")
    return cfg


@pytest.fixture
def subject(config):
    add_note_folder(config, "calculus", "Math/Calculus")
    return load_subject(config, "calculus")


def test_private_front_matter():
    assert is_private("---\nprivate: true\n---\nx")
    assert is_private("---\ntitle: a\nPrivate: Yes\n---\n")
    assert not is_private("---\nprivate: false\n---\n")
    assert not is_private("private: true\n")  # not front matter
    assert not is_private("text\n---\nprivate: true\n---\n")


def test_only_visible_non_private_notes_are_listed(config, subject):
    paths = {n.path for n in list_notes(config, subject)}
    assert paths == {"Math/Calculus/limits.md", "Math/Calculus/deep/epsilon.md"}
    titles = {n.path: n.title for n in list_notes(config, subject)}
    assert titles["Math/Calculus/limits.md"] == "Limits"
    assert titles["Math/Calculus/deep/epsilon.md"] == "epsilon"


def test_read_note(config, subject):
    assert read_note(config, subject, "Math/Calculus/limits.md").startswith("# Limits")


@pytest.mark.parametrize(
    "rel, message",
    [
        ("Math/Calculus/diary.md", "private"),
        ("Personal/journal.md", "not a note"),
        ("Math/Calculus/../../Personal/journal.md", "not a note"),
        ("Math/Calculus/escape.md", "not a note"),  # symlink out of the folder
        ("Math/Calculus/.trash/old.md", "not a note"),
        ("Math/Calculus/image.png", "not a note"),
        ("Math/Algebra/groups.md", "not a note"),
        ("/etc/passwd", "relative to the vault"),
    ],
)
def test_read_note_refuses(config, subject, rel, message):
    with pytest.raises(NotesError, match=message):
        read_note(config, subject, rel)


def test_long_notes_are_truncated(config, subject, vault):
    (vault / "Math/Calculus/long.md").write_text("x" * (MAX_NOTE_CHARS + 10))
    assert read_note(config, subject, "Math/Calculus/long.md").endswith("characters]")


def test_add_note_folder_keeps_comments_and_validates(config, subject):
    toml = (config.tutor_dir / "calculus/subject.toml").read_text()
    assert 'note_folders = ["Math/Calculus"]' in toml
    assert "# Vault folders (relative to the vault) the tutor may READ" in toml
    add_note_folder(config, "calculus", "Math/Algebra")
    assert [f.name for f in load_subject(config, "calculus").note_folders] == [
        "Calculus",
        "Algebra",
    ]
    for folder, message in [
        ("Math/Calculus", "already"),
        ("Nope", "not a folder"),
        ("../x", "outside the vault"),
        ("Tutor", "belongs to the tutor"),
    ]:
        with pytest.raises(ConfigError, match=message):
            add_note_folder(config, "calculus", folder)


def test_tools(config, subject):
    tools = TutorTools(config, subject)
    listing = tools.list_notes()
    assert "- Math/Calculus/limits.md: Limits" in listing and "diary" not in listing
    assert tools.read_note("Math/Calculus/limits.md").startswith(
        "Note Math/Calculus/limits.md:"
    )
    with pytest.raises(ToolError, match="private"):
        tools.read_note("Math/Calculus/diary.md")
    bare = load_subject(config, "calculus").__class__(
        **{**subject.__dict__, "note_folders": ()}
    )
    assert "velocity notes add calculus" in TutorTools(config, bare).list_notes()


def test_cli_notes(config, capsys):
    assert main(["notes", "list", "calculus"]) == 0
    assert "No note folders" in capsys.readouterr().out
    assert main(["notes", "add", "calculus", "Math/Calculus"]) == 0
    assert "2 readable notes" in capsys.readouterr().out
    assert main(["notes", "list", "calculus"]) == 0
    out = capsys.readouterr().out
    assert "Math/Calculus/limits.md" in out and "diary" not in out
    assert main(["notes", "add", "calculus", "../escape"]) == 1


def test_notes_are_never_written(config, subject, vault):
    """Listing and reading must not touch the files (mtime and content unchanged)."""
    before = {
        p: (p.stat().st_mtime_ns, p.read_bytes())
        for p in vault.rglob("*.md")
        if p.is_file() and not p.is_symlink() and "Tutor" not in p.parts
    }
    tools = TutorTools(config, subject)
    tools.list_notes()
    tools.read_note("Math/Calculus/limits.md")
    after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in before}
    assert before == after
    assert os.access(vault / "Math/Calculus/limits.md", os.R_OK)

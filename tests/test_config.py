from pathlib import Path

import pytest

from velocity.config import (
    Config,
    ConfigError,
    create_subject,
    list_subjects,
    load_config,
    load_subject,
)


@pytest.fixture
def vault(tmp_path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def config(vault) -> Config:
    return Config(vault.resolve())


def write_subject(config: Config, slug: str, body: str) -> None:
    d = config.tutor_dir / slug
    d.mkdir(parents=True)
    (d / "subject.toml").write_text(body)


def test_load_config_expands_and_validates_vault(tmp_path, vault):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'vault_path = "{vault}"\n')
    assert load_config(cfg).vault_path == vault.resolve()
    assert load_config(cfg).tutor_dir == vault.resolve() / "Tutor"


@pytest.mark.parametrize(
    "body, message",
    [
        ("", "missing 'vault_path'"),
        ('vault_path = "/does/not/exist"', "not a directory"),
        ("vault_path = ", "config.toml"),
    ],
)
def test_load_config_errors(tmp_path, body, message):
    cfg = tmp_path / "config.toml"
    cfg.write_text(body)
    with pytest.raises(ConfigError, match=message):
        load_config(cfg)


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_load_subject(config, vault):
    write_subject(
        config,
        "calculus",
        'name = "Calculus"\npersona = "Be terse."\n'
        'note_folders = ["Math/Calculus"]\n'
        '[[sources]]\nname = "Stewart"\npath = "~/books/stewart.pdf"\n'
        '[[sources]]\npath = "~/books/Spivak Calculus.pdf"\n',
    )
    s = load_subject(config, "calculus")
    assert (s.name, s.persona) == ("Calculus", "Be terse.")
    assert s.note_folders == (vault.resolve() / "Math/Calculus",)
    assert s.sources[0].path == Path("~/books/stewart.pdf").expanduser()
    assert [src.name for src in s.sources] == ["Stewart", "Spivak Calculus"]
    assert s.sessions_dir == config.tutor_dir / "calculus" / "sessions"


def test_subject_defaults(config):
    write_subject(config, "security", "")
    s = load_subject(config, "security")
    assert (s.name, s.persona, s.note_folders, s.sources) == ("security", "", (), ())


@pytest.mark.parametrize(
    "folder, message",
    [
        ("../outside", "outside the vault"),
        ("/etc", "relative to the vault"),
        ("Tutor/calculus", "belongs to the tutor"),
        ("Math/../Tutor", "belongs to the tutor"),
    ],
)
def test_note_folders_must_be_inside_vault_and_outside_tutor_dir(
    config, folder, message
):
    write_subject(config, "calculus", f'note_folders = ["{folder}"]\n')
    with pytest.raises(ConfigError, match=message):
        load_subject(config, "calculus")


def test_source_without_path(config):
    write_subject(config, "calculus", "[[sources]]\ntitle = 'x'\n")
    with pytest.raises(ConfigError, match="needs a 'path'"):
        load_subject(config, "calculus")


def test_create_and_list_subjects(config):
    assert list_subjects(config) == []
    s = create_subject(config, "Network Security")
    assert s.slug == "network-security"
    assert s.name == "Network Security"
    assert s.sessions_dir.is_dir()
    create_subject(config, "Calculus")
    assert list_subjects(config) == ["calculus", "network-security"]


def test_create_subject_refuses_to_overwrite(config):
    create_subject(config, "Calculus")
    with pytest.raises(ConfigError, match="already exists"):
        create_subject(config, "calculus")


def test_unknown_subject(config):
    with pytest.raises(ConfigError, match="not found"):
        load_subject(config, "nope")


@pytest.mark.parametrize("slug", ["../escape", "Calculus", "a/b", ""])
def test_subject_ids_cannot_escape_tutor_dir(config, slug):
    with pytest.raises(ConfigError):
        load_subject(config, slug)

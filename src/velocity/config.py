"""Global and per-subject configuration.

Global: ~/.config/velocity/config.toml (vault path).
Subject: <vault>/Tutor/<subject>/subject.toml (sources, readable note folders, persona extras).
"""

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

TUTOR_DIR_NAME = "Tutor"


class ConfigError(Exception):
    pass


def config_dir() -> Path:
    return (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "velocity"
    )


def data_dir() -> Path:
    """Tool-owned data outside the vault: workspaces, OCR cache, session records."""
    return (
        Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        / "velocity"
    )


@dataclass(frozen=True)
class Config:
    vault_path: Path

    @property
    def tutor_dir(self) -> Path:
        """The only part of the vault the tutor may write to."""
        return self.vault_path / TUTOR_DIR_NAME


@dataclass(frozen=True)
class SourceRef:
    path: Path


@dataclass(frozen=True)
class Subject:
    slug: str
    name: str
    dir: Path
    persona: str
    note_folders: tuple[Path, ...]  # absolute, inside the vault, outside Tutor/
    sources: tuple[SourceRef, ...]

    @property
    def sessions_dir(self) -> Path:
        return self.dir / "sessions"


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None


def load_config(path: Path | None = None) -> Config:
    path = path or config_dir() / "config.toml"
    data = _read_toml(path)
    if "vault_path" not in data:
        raise ConfigError(f"{path}: missing 'vault_path'")
    vault = Path(data["vault_path"]).expanduser()
    if not vault.is_dir():
        raise ConfigError(f"{path}: vault_path {vault} is not a directory")
    return Config(vault.resolve())


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ConfigError(f"subject name {name!r} has no usable characters")
    return slug


def _note_folder(config: Config, folder: str, where: Path) -> Path:
    if Path(folder).is_absolute():
        raise ConfigError(
            f"{where}: note folder {folder!r} must be relative to the vault"
        )
    resolved = (config.vault_path / folder).resolve()
    if not resolved.is_relative_to(config.vault_path):
        raise ConfigError(f"{where}: note folder {folder!r} is outside the vault")
    if resolved.is_relative_to(config.tutor_dir):
        raise ConfigError(
            f"{where}: note folder {folder!r} is inside {TUTOR_DIR_NAME}/, which belongs to the tutor"
        )
    return resolved


def load_subject(config: Config, slug: str) -> Subject:
    if slug != slugify(slug):
        raise ConfigError(
            f"invalid subject id {slug!r}; use lowercase letters, digits and dashes"
        )
    subject_dir = config.tutor_dir / slug
    path = subject_dir / "subject.toml"
    data = _read_toml(path)
    sources = []
    for entry in data.get("sources", []):
        if "path" not in entry:
            raise ConfigError(f"{path}: every [[sources]] entry needs a 'path'")
        sources.append(SourceRef(Path(entry["path"]).expanduser()))
    return Subject(
        slug=slug,
        name=data.get("name", slug),
        dir=subject_dir,
        persona=data.get("persona", ""),
        note_folders=tuple(
            _note_folder(config, f, path) for f in data.get("note_folders", [])
        ),
        sources=tuple(sources),
    )


def list_subjects(config: Config) -> list[str]:
    if not config.tutor_dir.is_dir():
        return []
    return sorted(p.parent.name for p in config.tutor_dir.glob("*/subject.toml"))


_SUBJECT_TEMPLATE = '''\
# Subject settings for velocity. Edit freely; the tutor reads this at session start.
name = "{name}"

# Extra instructions for the tutor in this subject (optional).
persona = """
"""

# Vault folders (relative to the vault) the tutor may READ. It never writes to them.
# Notes with `private: true` in their front matter are always skipped.
note_folders = []

# Optional sources to ground teaching in, e.g.:
# [[sources]]
# path = "~/books/textbook.pdf"
'''


def create_subject(config: Config, name: str) -> Subject:
    slug = slugify(name)
    path = config.tutor_dir / slug / "subject.toml"
    if path.exists():
        raise ConfigError(f"subject {slug!r} already exists at {path}")
    (path.parent / "sessions").mkdir(parents=True)
    path.write_text(
        _SUBJECT_TEMPLATE.format(name=name.replace('"', '\\"')), encoding="utf-8"
    )
    return load_subject(config, slug)

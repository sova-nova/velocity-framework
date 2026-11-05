"""Global and per-subject configuration.

Global: ~/.config/velocity/config.toml (vault path).
Subject: <vault>/Tutor/<subject>/subject.toml (sources, readable note folders, persona extras).
"""

import json
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
    # llama.cpp `llama-server`, for OCR without an NVIDIA GPU
    llama_cpp_binary: Path | None = None
    # [api] table: model and effort for `velocity chat`
    api_model: str | None = None
    api_effort: str | None = None

    @property
    def tutor_dir(self) -> Path:
        """The only part of the vault the tutor may write to."""
        return self.vault_path / TUTOR_DIR_NAME


@dataclass(frozen=True)
class SourceRef:
    path: Path
    name: str  # short label used in citations, e.g. "Stewart"


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
    llama = data.get("llama_cpp_binary")
    api = data.get("api", {})
    if not isinstance(api, dict):
        raise ConfigError(f"{path}: [api] must be a table")
    return Config(
        vault.resolve(),
        Path(llama).expanduser() if llama else None,
        api.get("model"),
        api.get("effort"),
    )


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
        source_path = Path(entry["path"]).expanduser()
        sources.append(SourceRef(source_path, entry.get("name") or source_path.stem))
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

# Optional sources to ground teaching in. Easiest: velocity source add <subject> <file.pdf> --name <Name>
# Or by hand:
# [[sources]]
# name = "Stewart"   # short label used in citations
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


SOURCE_SUFFIXES = {".pdf", ".md", ".markdown", ".html", ".htm", ".xhtml", ".epub"}


def add_source(
    config: Config, slug: str, path: Path, name: str | None = None
) -> SourceRef:
    """Append a source to a subject's subject.toml (keeping the rest of the file as is)."""
    subject = load_subject(config, slug)
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f"{path} is not a file")
    if path.suffix.lower() not in SOURCE_SUFFIXES:
        raise ConfigError(
            f"{path.name}: unsupported file type (use PDF, Markdown, HTML or EPUB)"
        )
    name = (name or path.stem).strip()
    if not name:
        raise ConfigError("source name is empty")
    if any(s.name == name for s in subject.sources):
        raise ConfigError(f"subject {slug!r} already has a source named {name!r}")
    if any(s.path == path for s in subject.sources):
        raise ConfigError(f"{path.name} is already a source of {slug!r}")
    toml = subject.dir / "subject.toml"
    text = toml.read_text(encoding="utf-8")
    entry = (
        "\n[[sources]]\n"
        f"name = {json.dumps(name, ensure_ascii=False)}\n"
        f"path = {json.dumps(path.as_posix(), ensure_ascii=False)}\n"
    )
    toml.write_text(text.rstrip("\n") + "\n" + entry, encoding="utf-8")
    added = load_subject(config, slug).sources[-1]
    assert added.path == path
    return added


def add_note_folder(config: Config, slug: str, folder: str) -> Path:
    """Let the tutor read notes in a vault folder (relative to the vault) for this subject."""
    subject = load_subject(config, slug)
    toml = subject.dir / "subject.toml"
    resolved = _note_folder(config, folder, toml)
    if not resolved.is_dir():
        raise ConfigError(
            f"{folder!r} is not a folder in the vault ({config.vault_path})"
        )
    if resolved in subject.note_folders:
        raise ConfigError(f"{folder!r} is already a note folder of {slug!r}")
    rel = resolved.relative_to(config.vault_path).as_posix()
    text = toml.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    if "note_folders" in data:
        # Rewrite just the note_folders line(s), keeping the rest of the file.
        new = "note_folders = " + json.dumps(
            [*data["note_folders"], rel], ensure_ascii=False
        )
        text, n = re.subn(
            r"^note_folders\s*=\s*\[[^\]]*\]",
            lambda _: new,
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if n != 1:
            raise ConfigError(f"{toml}: could not update note_folders; edit it by hand")
    else:
        text = f"note_folders = {json.dumps([rel], ensure_ascii=False)}\n" + text
    toml.write_text(text, encoding="utf-8")
    if resolved not in load_subject(config, slug).note_folders:
        raise ConfigError(f"{toml}: note folder was not saved; edit it by hand")
    return resolved

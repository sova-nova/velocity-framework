"""Global and per-subject configuration.

Global: ~/.config/velocity/config.toml (vault path).
Subject: <vault>/Tutor/<subject>/subject.toml (sources, readable note folders, persona extras).
"""

import json
import os
import re
import shutil
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
    archived: bool = False  # hidden in lists, everything kept; can be recovered

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
        archived=bool(data.get("archived", False)),
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


# A top-level `key = value` in subject.toml, with the value in any TOML string form,
# a boolean, or an array (possibly over several lines).
_VALUE = (
    r'(?:"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:[^"\\\n]|\\.)*"|\'[^\'\n]*\''
    r"|true|false|\[[^\]]*\])"
)


def _set_top_level(path: Path, key: str, value: str | None) -> None:
    """Set (or with None, remove) one top-level key in a TOML file, keeping the rest of
    the file (comments, layout, the [[sources]] tables) as it is. `value` is TOML."""
    text = path.read_text(encoding="utf-8")
    first_table = re.search(r"^\[", text, flags=re.MULTILINE)
    head, tail = (
        (text[: first_table.start()], text[first_table.start() :])
        if first_table
        else (text, "")
    )
    pattern = re.compile(rf"^{key}[ \t]*=[ \t]*{_VALUE}[ \t]*\n?", re.MULTILINE)
    line = f"{key} = {value}\n" if value is not None else ""
    if pattern.search(head):
        head = pattern.sub(lambda _: line, head, count=1)
    elif value is not None:
        # After the name line if there is one, else at the top.
        name = re.search(r"^name[ \t]*=.*\n", head, flags=re.MULTILINE)
        at = name.end() if name else 0
        head = head[:at] + line + head[at:]
    new = head + tail
    tomllib.loads(new)  # never write a file velocity can't read back
    path.write_text(new, encoding="utf-8")


def _toml_string(text: str) -> str:
    """A TOML string that reads back exactly as `text` (JSON strings are valid TOML)."""
    return json.dumps(text, ensure_ascii=False)


def _toml_text_block(text: str) -> str:
    """Several lines of text, readable when the learner opens subject.toml."""
    text = text.strip("\n")
    if "'''" in text or not text:
        return _toml_string(text)
    return f"'''\n{text}\n'''"


def set_subject_name(config: Config, slug: str, name: str) -> Subject:
    """Rename a subject (its display name; the folder and the ids keep the old name)."""
    name = name.strip()
    if not name:
        raise ConfigError("the subject's name is empty")
    _set_top_level(subject_toml(config, slug), "name", _toml_string(name))
    return load_subject(config, slug)


def set_persona(config: Config, slug: str, persona: str) -> Subject:
    """The extra instructions for the tutor in this subject."""
    _set_top_level(subject_toml(config, slug), "persona", _toml_text_block(persona))
    subject = load_subject(config, slug)
    if subject.persona.strip() != persona.strip():
        raise ConfigError("the instructions were not saved; edit subject.toml by hand")
    return subject


def set_note_folders(config: Config, slug: str, folders: list[str]) -> Subject:
    """Replace the vault folders the tutor may read (relative to the vault)."""
    toml = subject_toml(config, slug)
    rels = []
    for folder in folders:
        resolved = _note_folder(config, folder, toml)
        if not resolved.is_dir():
            raise ConfigError(f"{folder!r} is not a folder in the vault")
        rel = resolved.relative_to(config.vault_path).as_posix()
        if rel not in rels:
            rels.append(rel)
    _set_top_level(toml, "note_folders", json.dumps(rels, ensure_ascii=False))
    return load_subject(config, slug)


def set_archived(config: Config, slug: str, archived: bool) -> Subject:
    """Archive a subject (hidden in lists, everything kept) or recover it."""
    _set_top_level(subject_toml(config, slug), "archived", "true" if archived else None)
    return load_subject(config, slug)


def delete_subject(config: Config, slug: str) -> None:
    """Delete an archived subject: its folder in Tutor/ (settings, session logs, progress)
    and velocity's records and workspace for it. Source files are kept."""
    subject = load_subject(config, slug)
    if not subject.archived:
        raise ConfigError("only archived subjects can be deleted; archive it first")
    folder = subject.dir.resolve()
    if folder.parent != config.tutor_dir.resolve():
        raise ConfigError(f"{folder} is not a subject folder in {TUTOR_DIR_NAME}/")
    shutil.rmtree(folder)
    for leftover in (data_dir() / "records" / slug, data_dir() / "workspaces" / slug):
        shutil.rmtree(leftover, ignore_errors=True)


def subject_toml(config: Config, slug: str) -> Path:
    path = load_subject(config, slug).dir / "subject.toml"
    if not path.is_file():
        raise ConfigError(f"no subject {slug!r}")
    return path


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

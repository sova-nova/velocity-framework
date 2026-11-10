"""Global and per-subject configuration.

Global: ~/.config/velocity/config.toml (vault path).
Subject: <vault>/Tutor/<subject>/subject.toml (sources, readable note folders, persona extras).
"""

import hashlib
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
    color: str | None = None  # "#rrggbb" from subject.toml; else picked from the name

    @property
    def sessions_dir(self) -> Path:
        return self.dir / "sessions"

    @property
    def display_color(self) -> str:
        """The subject's color on the page: its own, or one of SUBJECT_COLORS picked
        from its id, so it stays the same without being stored."""
        if self.color:
            return self.color
        index = int(hashlib.sha256(self.slug.encode()).hexdigest(), 16)
        return SUBJECT_COLORS[index % len(SUBJECT_COLORS)]


# Eight colors that read on the Chalkboard palette in light and dark (M10).
SUBJECT_COLORS = (
    "#2a9d8f",  # teal
    "#e76f51",  # coral
    "#5468d4",  # indigo
    "#e9a23b",  # amber
    "#9b5de5",  # plum
    "#4f9d4a",  # green
    "#d6457d",  # rose
    "#5c7a99",  # slate
)
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


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
        color=data["color"] if _COLOR.match(str(data.get("color", ""))) else None,
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
    remember_library_file(path)
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


def _set_in_table(path: Path, table: str, key: str, value: str | None) -> None:
    """Set (or with None, remove) `key` in the `[table]` of a TOML file, adding the
    table at the end if it isn't there; the rest of the file stays as it is."""
    text = path.read_text(encoding="utf-8")
    header = re.search(rf"^\[{re.escape(table)}\][ \t]*$", text, flags=re.MULTILINE)
    line = f"{key} = {value}\n" if value is not None else ""
    if header is None:
        if value is None:
            return
        new = text.rstrip("\n") + f"\n\n[{table}]\n{line}"
    else:
        after = re.search(r"^\[", text[header.end() :], flags=re.MULTILINE)
        end = header.end() + after.start() if after else len(text)
        body = text[header.end() : end]
        pattern = re.compile(rf"^{key}[ \t]*=[ \t]*{_VALUE}[ \t]*\n?", re.MULTILINE)
        if pattern.search(body):
            body = pattern.sub(lambda _: line, body, count=1)
        elif value is not None:
            body = "\n" + line + body.lstrip("\n")
        new = text[: header.end()] + body + text[end:]
    tomllib.loads(new)
    path.write_text(new, encoding="utf-8")


# -- global settings (config.toml), also edited on the web page (M10) ---------------------

EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODEL_SHORT_NAMES = ("opus", "sonnet", "haiku")

_CONFIG_TEMPLATE = """\
# velocity settings. Edit freely, or use the settings on the web page (velocity web).

# Your notes folder: an Obsidian vault, or any folder. velocity keeps its own files in a
# Tutor/ folder inside it, and only ever reads the rest.
vault_path = {vault}

# llama.cpp's llama-server, for reading PDFs (OCR) without an NVIDIA GPU (optional):
# llama_cpp_binary = "~/llama.cpp/llama-server"

# Defaults for the API engine (velocity chat, and API chats on the web page):
# [api]
# model = "sonnet"   # opus, sonnet, haiku, or a full model id
# effort = "medium"  # low, medium, high, xhigh, max
"""


def config_path() -> Path:
    return config_dir() / "config.toml"


def save_settings(changes: dict, create_vault: bool = False) -> Config:
    """Change the global settings in config.toml (creating it on first run). `changes`
    holds only what changes: vault_path, llama_cpp_binary, api_model, api_effort; an
    empty value removes an optional setting. Checked before anything is written."""
    lines: dict[str, tuple[str | None, str | None]] = {}  # key -> (table, TOML value)
    if "vault_path" in changes:
        vault = Path(str(changes["vault_path"]).strip()).expanduser()
        if not str(changes["vault_path"]).strip() or not vault.is_absolute():
            raise ConfigError("Give the full path of your notes folder.")
        if not vault.is_dir():
            if not create_vault:
                raise ConfigError(f"{vault} is not a folder.")
            vault.mkdir(parents=True)
        lines["vault_path"] = (None, _toml_string(vault.resolve().as_posix()))
    if "llama_cpp_binary" in changes:
        llama = str(changes["llama_cpp_binary"] or "").strip()
        if llama and not Path(llama).expanduser().is_file():
            raise ConfigError(f"{llama} is not a file.")
        lines["llama_cpp_binary"] = (None, _toml_string(llama) if llama else None)
    if "api_model" in changes:
        model = str(changes["api_model"] or "").strip()
        if model and model not in MODEL_SHORT_NAMES and not model.startswith("claude-"):
            raise ConfigError(
                f"Unknown model {model!r}: use opus, sonnet, haiku or a full id."
            )
        lines["model"] = ("api", _toml_string(model) if model else None)
    if "api_effort" in changes:
        effort = str(changes["api_effort"] or "").strip()
        if effort and effort not in EFFORTS:
            raise ConfigError(f"Unknown effort {effort!r}: use {', '.join(EFFORTS)}.")
        lines["effort"] = ("api", _toml_string(effort) if effort else None)

    path = config_path()
    if not path.exists():
        if "vault_path" not in lines:
            raise ConfigError("Choose your notes folder first.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _CONFIG_TEMPLATE.format(vault=lines.pop("vault_path")[1]), encoding="utf-8"
        )
    for key, (table, value) in lines.items():
        if table:
            _set_in_table(path, table, key, value)
        else:
            _set_top_level(path, key, value)
    return load_config()


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


def set_color(config: Config, slug: str, color: str | None) -> Subject:
    """The subject's color on the page ("#rrggbb"); None goes back to the picked one."""
    if color is not None and not _COLOR.match(color):
        raise ConfigError(f"{color!r} is not a color like #2a9d8f")
    value = _toml_string(color.lower()) if color is not None else None
    _set_top_level(subject_toml(config, slug), "color", value)
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
    for ref in subject.sources:  # its books stay in the library
        if ref.path.is_file():
            remember_library_file(ref.path)
    shutil.rmtree(folder)
    for leftover in (data_dir() / "records" / slug, data_dir() / "workspaces" / slug):
        shutil.rmtree(leftover, ignore_errors=True)


def subject_toml(config: Config, slug: str) -> Path:
    path = load_subject(config, slug).dir / "subject.toml"
    if not path.is_file():
        raise ConfigError(f"no subject {slug!r}")
    return path


def _library_index() -> Path:
    return data_dir() / "library" / "index.json"


def remember_library_file(path: Path) -> None:
    """Keep a source file in the library (library.py) even when no subject uses it any
    more, e.g. after it was removed or its subject deleted."""
    files = library_files()
    path = path.resolve()
    if path in files:
        return
    index = _library_index()
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(
        json.dumps([p.as_posix() for p in [*files, path]], indent=1), encoding="utf-8"
    )


def forget_library_file(path: Path) -> None:
    """Drop a file from the library index (the file itself is not touched here)."""
    path = path.resolve()
    files = [p for p in library_files() if p.resolve() != path]
    index = _library_index()
    if index.exists():
        index.write_text(
            json.dumps([p.as_posix() for p in files], indent=1), encoding="utf-8"
        )


def library_files() -> list[Path]:
    """Every source file ever added to a subject (that the index remembers)."""
    try:
        return [
            Path(p) for p in json.loads(_library_index().read_text(encoding="utf-8"))
        ]
    except (OSError, ValueError):
        return []


def _source_table(text: str, name: str) -> tuple[int, int]:
    """(start, end) of the [[sources]] table for the source called `name` (its `name`,
    or its file name when it has none), up to the next table."""
    headers = list(re.finditer(r"^\[.*$", text, flags=re.MULTILINE))
    for i, header in enumerate(headers):
        if header.group().strip() != "[[sources]]":
            continue
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        table = tomllib.loads(text[header.start() : end])["sources"][0]
        own = table.get("name") or Path(table.get("path", "")).expanduser().stem
        if own == name:
            return header.start(), end
    raise ConfigError(f"no source named {name!r}")


def remove_source(config: Config, slug: str, name: str) -> None:
    """Take a source out of a subject (subject.toml). The file itself is kept, so it stays
    in the library, prepared pages included."""
    toml = subject_toml(config, slug)
    for ref in load_subject(config, slug).sources:
        if ref.name == name and ref.path.is_file():
            remember_library_file(ref.path)  # sources added before the index existed
    text = toml.read_text(encoding="utf-8")
    start, end = _source_table(text, name)
    new = (
        text[:start].rstrip("\n")
        + "\n"
        + ("\n" + text[end:].lstrip("\n") if text[end:].strip() else "")
    )
    tomllib.loads(new)
    toml.write_text(new, encoding="utf-8")
    if any(s.name == name for s in load_subject(config, slug).sources):
        raise ConfigError(f"{toml}: the source was not removed; edit it by hand")


def rename_source(config: Config, slug: str, name: str, new_name: str) -> SourceRef:
    """Change the name a subject cites a source by (e.g. "Stewart"). Earlier session logs
    keep citing the old name."""
    new_name = new_name.strip()
    if not new_name:
        raise ConfigError("the source's name is empty")
    subject = load_subject(config, slug)
    if any(s.name == new_name for s in subject.sources if s.name != name):
        raise ConfigError(f"{subject.name} already has a source named {new_name!r}")
    toml = subject.dir / "subject.toml"
    text = toml.read_text(encoding="utf-8")
    start, end = _source_table(text, name)
    table = text[start:end]
    line = f"name = {_toml_string(new_name)}"
    if re.search(r"^name[ \t]*=.*$", table, flags=re.MULTILINE):
        table = re.sub(
            r"^name[ \t]*=.*$", lambda _: line, table, count=1, flags=re.MULTILINE
        )
    else:
        table = table.replace("[[sources]]", f"[[sources]]\n{line}", 1)
    new = text[:start] + table + text[end:]
    tomllib.loads(new)
    toml.write_text(new, encoding="utf-8")
    renamed = next(
        (s for s in load_subject(config, slug).sources if s.name == new_name), None
    )
    if renamed is None:
        raise ConfigError(f"{toml}: the source was not renamed; edit it by hand")
    return renamed


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

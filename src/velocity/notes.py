"""Read-only access to the learner's notes.

Only markdown files inside the subject's note folders are visible. Notes with `private: true`
(or `yes`) in their front matter are never listed or read. Nothing here writes to the vault.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from velocity.config import Config, Subject

MAX_NOTE_CHARS = 40_000
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_PRIVATE = re.compile(
    r"^private\s*:\s*['\"]?(true|yes)['\"]?\s*$", re.IGNORECASE | re.MULTILINE
)


class NotesError(Exception):
    pass


@dataclass(frozen=True)
class NoteInfo:
    path: str  # relative to the vault, with forward slashes
    title: str
    modified: date


def is_private(text: str) -> bool:
    m = _FRONT_MATTER.match(text.replace("\r\n", "\n"))
    return bool(m and _PRIVATE.search(m[1]))


def _title(text: str, path: Path) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return m[1].strip() if m else path.stem


def _visible(config: Config, subject: Subject, path: Path) -> bool:
    """A real markdown file inside one of the subject's note folders (after resolving links)."""
    resolved = path.resolve()
    if resolved.suffix.lower() != ".md" or not resolved.is_file():
        return False
    if resolved.is_relative_to(config.tutor_dir):
        return False
    for folder in subject.note_folders:
        if resolved.is_relative_to(folder):
            # Hidden folders such as .obsidian or .trash are not notes.
            return not any(
                p.startswith(".") for p in resolved.relative_to(folder).parts
            )
    return False


def list_notes(config: Config, subject: Subject) -> list[NoteInfo]:
    """Visible, non-private notes, most recently modified first."""
    seen: set[Path] = set()
    notes = []
    for folder in subject.note_folders:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*.md"):
            if path in seen or not _visible(config, subject, path):
                continue
            seen.add(path)
            text = path.read_text(encoding="utf-8", errors="replace")
            if is_private(text):
                continue
            notes.append(
                NoteInfo(
                    path.relative_to(config.vault_path).as_posix(),
                    _title(text, path),
                    datetime.fromtimestamp(path.stat().st_mtime).date(),
                )
            )
    return sorted(notes, key=lambda n: (n.modified, n.path), reverse=True)


def read_note(config: Config, subject: Subject, rel_path: str) -> str:
    """The text of one note, given its path relative to the vault (as list_notes returns it)."""
    if not subject.note_folders:
        raise NotesError("This subject has no note folders configured.")
    # Any root counts: on Windows "/etc/passwd" has no drive, so it isn't is_absolute().
    if Path(rel_path).is_absolute() or Path(rel_path).anchor:
        raise NotesError(
            "Give the note's path relative to the vault, as list_notes shows it."
        )
    path = config.vault_path / rel_path
    if not _visible(config, subject, path):
        raise NotesError(
            f"{rel_path!r} is not a note the tutor may read. Use list_notes."
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    if is_private(text):
        raise NotesError(f"{rel_path!r} is marked private.")
    if len(text) > MAX_NOTE_CHARS:
        text = (
            text[:MAX_NOTE_CHARS]
            + f"\n\n[... truncated at {MAX_NOTE_CHARS} characters]"
        )
    return text

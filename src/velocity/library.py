"""The sources library (M10, 2026-11-09): every book or notes file any subject uses, and
every file uploaded from the page, listed once, so another subject can add it without
uploading (or OCR'ing) it again. The OCR cache is per file, so prepared pages are shared.

Entries are named by an id (a hash of the file's path), never by the path: adding from
the library can only add a file that is already in it.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from velocity.config import (
    SOURCE_SUFFIXES,
    Config,
    ConfigError,
    library_files,
    list_subjects,
    load_subject,
)
from velocity.uploads import legacy_upload_dirs, library_dir


@dataclass(frozen=True)
class Use:
    subject_slug: str
    subject_name: str
    source_name: str  # the name the subject cites it by
    archived: bool


@dataclass
class LibraryEntry:
    id: str
    path: Path
    uses: list[Use] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return "pdf" if self.path.suffix.lower() == ".pdf" else "text"

    @property
    def suggested_name(self) -> str:
        """The name another subject already gives it, else the file name."""
        return self.uses[0].source_name if self.uses else self.path.stem

    def as_json(self) -> dict:
        return {
            "id": self.id,
            "filename": self.path.name,
            "kind": self.kind,
            "size": self.path.stat().st_size,
            "suggested_name": self.suggested_name,
            "uses": [
                {
                    "subject": u.subject_slug,
                    "subject_name": u.subject_name,
                    "name": u.source_name,
                    "archived": u.archived,
                }
                for u in self.uses
            ],
        }


def entry_id(path: Path) -> str:
    return hashlib.sha256(path.resolve().as_posix().encode()).hexdigest()[:16]


def library(config: Config) -> list[LibraryEntry]:
    """All books and notes files velocity knows, by file name."""
    entries: dict[Path, LibraryEntry] = {}

    def entry(path: Path) -> LibraryEntry:
        path = path.resolve()
        if path not in entries:
            entries[path] = LibraryEntry(entry_id(path), path)
        return entries[path]

    for slug in list_subjects(config):
        try:
            subject = load_subject(config, slug)
        except ConfigError:
            continue  # a subject.toml with an error: its sources aren't listed
        for ref in subject.sources:
            if ref.path.is_file():
                entry(ref.path).uses.append(
                    Use(slug, subject.name, ref.name, subject.archived)
                )
    for path in library_files():  # removed from every subject, or its subject deleted
        if path.is_file():
            entry(path)
    for folder in [library_dir(), *legacy_upload_dirs()]:
        if folder.is_dir():
            for path in folder.iterdir():
                if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES:
                    entry(path)
    return sorted(entries.values(), key=lambda e: e.path.name.lower())


def find(config: Config, entry: str) -> LibraryEntry:
    for e in library(config):
        if e.id == entry:
            return e
    raise ConfigError("That book is no longer in the library.")

"""The sources library (M10, 2026-11-09): every book or notes file any subject uses, and
every file uploaded from the page, listed once, so another subject can add it without
uploading (or OCR'ing) it again. The OCR cache is per file, so prepared pages are shared.

Entries are named by an id (a hash of the file's path), never by the path: adding from
the library can only add a file that is already in it.
"""

import filecmp
import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from velocity.config import (
    SOURCE_SUFFIXES,
    Config,
    ConfigError,
    forget_library_file,
    library_files,
    list_subjects,
    load_subject,
)
from velocity.pdf_source import PdfSource, default_cache_dir
from velocity.prepare import running
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

    @property
    def managed(self) -> bool:
        """A copy velocity made (an upload), which velocity may delete; any other file
        is the learner's own and is never deleted."""
        return any(
            self.path.is_relative_to(folder.resolve())
            for folder in [library_dir(), *legacy_upload_dirs()]
        )

    @property
    def cache_dir(self) -> Path | None:
        """Where its prepared (OCR'd) pages are kept: per file name and size, so copies
        of the same book share them."""
        return default_cache_dir(self.path) if self.kind == "pdf" else None

    def prepared(self) -> dict | None:
        """{"pages": total, "done": OCR'd} for PDFs."""
        if self.kind != "pdf":
            return None
        import pymupdf

        try:
            with pymupdf.open(self.path) as doc:
                total = doc.page_count
        except Exception:  # noqa: BLE001 - a damaged PDF: no page count
            return None
        pages = self.cache_dir / "pages"
        done = len(list(pages.glob("*.md"))) if pages.is_dir() else 0
        return {"pages": total, "done": min(done, total)}

    def as_json(self) -> dict:
        return {
            "id": self.id,
            "filename": self.path.name,
            "kind": self.kind,
            "size": self.path.stat().st_size,
            "managed": self.managed,
            "prepared": self.prepared(),
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


def duplicates(entries: list[LibraryEntry]) -> dict[str, str]:
    """{id: id of the earlier copy} for files with the same name and size, which are
    almost certainly the same book uploaded twice."""
    first: dict[tuple[str, int], str] = {}
    out = {}
    for e in entries:
        key = (e.path.name, e.path.stat().st_size)
        if key in first:
            out[e.id] = first[key]
        else:
            first[key] = e.id
    return out


def same_book(config: Config, path: Path) -> LibraryEntry | None:
    """Another entry with exactly this file's content (an upload of a book the library
    already has, under any name), or None."""
    path = path.resolve()
    size = path.stat().st_size
    for e in library(config):
        same_size = e.path != path and e.path.stat().st_size == size
        if same_size and filecmp.cmp(e.path, path, shallow=False):
            return e
    return None


def remove(config: Config, entry: str) -> dict:
    """Remove an unused book from the library. A copy velocity made is deleted with its
    prepared pages (unless another copy shares them); a file of the learner's elsewhere
    is only forgotten, never deleted. Returns what was done."""
    entries = library(config)
    e = next((x for x in entries if x.id == entry), None)
    if e is None:
        raise ConfigError("That book is no longer in the library.")
    if e.uses:
        names = ", ".join(u.subject_name for u in e.uses)
        raise ConfigError(
            f"{e.path.name} is still used by {names}; remove it there first."
        )
    if e.kind == "pdf" and running(PdfSource(e.path, ocr=None)):
        raise ConfigError(f"{e.path.name} is being prepared; cancel that first.")
    forget_library_file(e.path)
    if not e.managed:
        return {"filename": e.path.name, "deleted": False, "freed": 0}
    freed = e.path.stat().st_size
    shared = e.cache_dir and any(
        x.cache_dir == e.cache_dir for x in entries if x.id != e.id
    )
    if e.cache_dir and e.cache_dir.is_dir() and not shared:
        freed += sum(f.stat().st_size for f in e.cache_dir.rglob("*") if f.is_file())
        shutil.rmtree(e.cache_dir)
    e.path.unlink()
    return {"filename": e.path.name, "deleted": True, "freed": freed}

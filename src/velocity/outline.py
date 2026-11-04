"""Book outline from a PDF's embedded table of contents, keyed by stable section IDs."""

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# (level, title, 1-indexed PDF start page), as returned by get_toc()
TocEntry = tuple[int, str, int]


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    level: int
    parent_id: str | None
    start_page: int  # 1-indexed PDF page, inclusive
    end_page: int  # 1-indexed PDF page, inclusive; may be shared with the next section


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _section_id(title: str, chapter: str | None) -> str:
    if m := re.match(r"Ch (\d+):", title):
        return f"ch{m[1]}"
    if m := re.match(r"(\d+\.\d+):", title):
        return m[1]
    if m := re.match(r"Appendix ([A-Z]):", title):
        return f"app-{m[1].lower()}"
    slug = _slug(title)
    return f"{chapter}.{slug}" if chapter else slug


def build_outline(toc: list[TocEntry], page_count: int) -> list[Section]:
    """Turn raw TOC entries into sections with stable IDs and inclusive page ranges."""
    entries = [
        (level, title.replace("\x00", "").strip(), page) for level, title, page in toc
    ]
    sections: list[Section] = []
    parents: dict[int, str] = {}
    chapter: str | None = None
    for i, (level, title, start) in enumerate(entries):
        if level == 1:
            m = re.match(r"Ch (\d+):", title)
            chapter = m[1] if m else None
        sid = _section_id(title, chapter if level > 1 else None)
        if any(s.id == sid for s in sections):
            raise ValueError(f"duplicate section id {sid!r} for title {title!r}")

        end = page_count
        for next_level, _, next_start in entries[i + 1 :]:
            if next_level <= level:
                # Sibling sections can share a page; a new top-level part starts on a fresh page.
                end = (
                    next_start if next_level == level and level > 1 else next_start - 1
                )
                break
        parents[level] = sid
        sections.append(
            Section(
                sid,
                title,
                level,
                parents.get(level - 1) if level > 1 else None,
                start,
                max(start, end),
            )
        )
    return sections


def get_outline(pdf_path: str | Path) -> list[Section]:
    """Outline of the book at pdf_path."""
    with pymupdf.open(pdf_path) as doc:
        return build_outline(doc.get_toc(), doc.page_count)

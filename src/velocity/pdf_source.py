"""A PDF textbook as a source: outline plus lazily OCR'd, cached section text."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from velocity.config import data_dir
from velocity.ocr import PageOcr
from velocity.outline import Section, get_outline

_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_TITLE_PREFIX = re.compile(r"^(Ch \d+|\d+\.\d+|Appendix [A-Z]):\s*")


@dataclass(frozen=True)
class SectionText:
    id: str
    title: str
    first_page: int  # 1-indexed PDF pages that were read, inclusive
    last_page: int
    markdown: str
    warnings: list[str] = field(default_factory=list)


def default_cache_dir(pdf_path: Path) -> Path:
    stem = re.sub(r"[^a-z0-9]+", "-", pdf_path.stem.lower()).strip("-")[:60]
    return data_dir() / "sources" / f"{stem}-{pdf_path.stat().st_size}"


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _find_heading(markdown: str, section: Section, after: int = 0) -> int | None:
    """Offset of the first markdown heading at or after `after` that names `section`."""
    wanted = _norm(_TITLE_PREFIX.sub("", section.title))
    for m in _HEADING.finditer(markdown, after):
        if wanted and wanted in _norm(m[1]):
            return m.start()
    return None


class PdfSource:
    def __init__(
        self,
        pdf_path: str | Path,
        ocr: PageOcr,
        cache_dir: Path | None = None,
        outline: list[Section] | None = None,
    ) -> None:
        self.pdf_path = Path(pdf_path)
        self.cache_dir = cache_dir or default_cache_dir(self.pdf_path)
        self._ocr = ocr
        self._outline = outline

    def outline(self) -> list[Section]:
        if self._outline is None:
            self._outline = get_outline(self.pdf_path)
        return self._outline

    def read_section(self, section_id: str) -> SectionText:
        """Text of one section as markdown with LaTeX math, from its heading to the next entry's."""
        outline = self.outline()
        index = next((i for i, s in enumerate(outline) if s.id == section_id), None)
        if index is None:
            raise KeyError(f"unknown section id {section_id!r}")
        section = outline[index]
        following = outline[index + 1] if index + 1 < len(outline) else None
        last_page = following.start_page if following else _outline_end(outline)

        markdown = "\n\n".join(self._pages(section.start_page, last_page))
        warnings = []
        start = _find_heading(markdown, section)
        if start is None:
            start = 0
            warnings.append(
                f"Heading for {section.title!r} not found; text may begin with the end of the previous section."
            )
        end = None
        if following:
            end = _find_heading(markdown, following, after=start + 1)
            if end is None:
                warnings.append(
                    f"Heading for next entry {following.title!r} not found; text may run into it."
                )
        return SectionText(
            section.id,
            section.title,
            section.start_page,
            last_page,
            markdown[start:end].strip(),
            warnings,
        )

    def _pages(self, first: int, last: int) -> list[str]:
        """Markdown of pages first..last, OCR'ing and caching any not yet cached."""
        page_dir = self.cache_dir / "pages"
        missing = [
            p for p in range(first, last + 1) if not (page_dir / f"{p}.md").exists()
        ]
        for run_first, run_last in _runs(missing):
            pages = self._ocr(
                self.pdf_path, run_first, run_last, self.cache_dir / "images"
            )
            page_dir.mkdir(parents=True, exist_ok=True)
            for p in range(run_first, run_last + 1):
                # A page marker produced no output for is cached empty, so it isn't retried forever.
                (page_dir / f"{p}.md").write_text(pages.get(p, ""), encoding="utf-8")
        return [
            (page_dir / f"{p}.md").read_text(encoding="utf-8")
            for p in range(first, last + 1)
        ]


def _outline_end(outline: list[Section]) -> int:
    return max(s.end_page for s in outline)


def _runs(pages: list[int]) -> list[tuple[int, int]]:
    """Group sorted page numbers into contiguous (first, last) runs."""
    runs: list[tuple[int, int]] = []
    for p in pages:
        if runs and runs[-1][1] == p - 1:
            runs[-1] = (runs[-1][0], p)
        else:
            runs.append((p, p))
    return runs

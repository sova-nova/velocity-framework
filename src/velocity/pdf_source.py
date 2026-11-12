"""A PDF textbook as a source: outline plus lazily OCR'd, cached section text."""

import contextlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from velocity import filelock
from velocity.config import data_dir
from velocity.dropcheck import Region, find_drops, repair, text_lines
from velocity.ocr import OcrPage, PageOcr
from velocity.outline import Section, get_outline
from velocity.search import Chunk, section_at

_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
# Pixels on a page image's longer side. Checked on Stewart p. 137 (2026-11-09): at
# 1,100 px a limit's subscript is barely legible; 1,400 px is clear. About 2,250 image
# tokens per page (pixels / 750), well under the models' 2,576 px limit.
PAGE_IMAGE_EDGE = 1400
# Pages OCR'd per batch when preparing a source ahead of time (about a minute on the
# user's GPU): a read_section waiting for the OCR lock waits at most this long.
PREPARE_BATCH = 10
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
        self._pages_text: list[str] | None = None

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
        warnings = self._repair_warnings(section.start_page, last_page)
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

    @property
    def page_count(self) -> int:
        import pymupdf

        with pymupdf.open(self.pdf_path) as doc:
            return doc.page_count

    def render_page(self, page: int, long_edge: int = PAGE_IMAGE_EDGE) -> bytes:
        """A page as a PNG image, `long_edge` pixels on its longer side (1-indexed page).
        Takes about 0.1 s, with no OCR."""
        import pymupdf

        with pymupdf.open(self.pdf_path) as doc:
            if not 1 <= page <= doc.page_count:
                raise IndexError(f"page {page} is not in 1-{doc.page_count}")
            pdf_page = doc[page - 1]
            zoom = long_edge / max(pdf_page.rect.width, pdf_page.rect.height)
            return pdf_page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")

    def search_chunks(self) -> list[Chunk]:
        """Every page's text for search, under the section it belongs to: the text layer,
        or the OCR'd text where a page has no text layer (scans) and it is cached."""
        outline = self.outline()
        chunks = []
        for page, text in enumerate(self._text_layer(), start=1):
            if len(text.strip()) < 20:
                cached = self._page_dir / f"{page}.md"
                text = cached.read_text(encoding="utf-8") if cached.exists() else ""
            section = section_at(outline, page)
            if section and text.strip():
                chunks.append(Chunk(section.id, section.title, page, text))
        return chunks

    def _text_layer(self) -> list[str]:
        """The PDF's text layer, page by page, cached on disk after the first read (a
        1,400-page textbook takes a few seconds)."""
        if self._pages_text is not None:
            return self._pages_text
        cached = self.cache_dir / "text-layer.json"
        if cached.exists():
            self._pages_text = json.loads(cached.read_text(encoding="utf-8"))
            return self._pages_text
        import pymupdf

        with pymupdf.open(self.pdf_path) as doc:
            self._pages_text = [page.get_text() for page in doc]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(self._pages_text), encoding="utf-8")
        return self._pages_text

    @property
    def _page_dir(self) -> Path:
        return self.cache_dir / "pages"

    def missing_pages(self, first: int, last: int) -> list[int]:
        """Pages in first..last that aren't OCR'd yet."""
        return [
            p
            for p in range(first, last + 1)
            if not (self._page_dir / f"{p}.md").exists()
        ]

    def _pages(self, first: int, last: int) -> list[str]:
        """Markdown of pages first..last, OCR'ing and caching any not yet cached. While
        another process OCRs (a prepare job), this waits for its current batch, then
        OCRs only what is still missing."""
        if self.missing_pages(first, last):
            with self.ocr_lock():
                for run_first, run_last in _runs(self.missing_pages(first, last)):
                    self._ocr_run(run_first, run_last)
        return [
            (self._page_dir / f"{p}.md").read_text(encoding="utf-8")
            for p in range(first, last + 1)
        ]

    def unchecked_pages(self) -> list[int]:
        """Cached pages OCR'd before the dropped-text check existed."""
        if not self._page_dir.is_dir():
            return []
        return sorted(
            int(md.stem)
            for md in self._page_dir.glob("*.md")
            if not md.with_suffix(".check.json").exists()
        )

    def cached_pages(self) -> list[int]:
        if not self._page_dir.is_dir():
            return []
        return sorted(int(md.stem) for md in self._page_dir.glob("*.md"))

    def recheck(self, all_pages: bool = False) -> list[int]:
        """OCR cached pages again with the dropped-text check: the unchecked ones, or all."""
        pages = self.cached_pages() if all_pages else self.unchecked_pages()
        return self.ocr_again(pages)

    def prepare(
        self,
        first: int,
        last: int,
        batch: int = PREPARE_BATCH,
        on_batch: Callable[[list[int]], None] | None = None,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> list[int]:
        """OCR the pages in first..last that aren't cached yet, ahead of reading them.

        Works in batches of up to `batch` consecutive pages, taking the OCR lock for one
        batch at a time, so a `read_section` in another process waits at most one batch.
        Calls `on_batch` with the pages of each finished batch, and stops between batches
        when `should_stop()` is true. Returns the pages it OCR'd.
        """
        done: list[int] = []
        for run_first, run_last in _runs(self.missing_pages(first, last)):
            for start in range(run_first, run_last + 1, batch):
                if should_stop():
                    return done
                end = min(start + batch - 1, run_last)
                with self.ocr_lock():
                    # Another process (the tutor) may have OCR'd some of them meanwhile.
                    pages = self.missing_pages(start, end)
                    for sub_first, sub_last in _runs(pages):
                        self._ocr_run(sub_first, sub_last)
                done += pages
                if on_batch:
                    on_batch(pages)
        return done

    def ocr_again(
        self,
        pages: list[int],
        batch: int = PREPARE_BATCH,
        on_batch: Callable[[list[int]], None] | None = None,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> list[int]:
        """OCR cached pages again (with the dropped-text check), in batches under the OCR
        lock like `prepare`. Returns the pages done."""
        done: list[int] = []
        for run_first, run_last in _runs(sorted(pages)):
            for start in range(run_first, run_last + 1, batch):
                if should_stop():
                    return done
                end = min(start + batch - 1, run_last)
                with self.ocr_lock():
                    self._ocr_run(start, end)
                batch_pages = list(range(start, end + 1))
                done += batch_pages
                if on_batch:
                    on_batch(batch_pages)
        return done

    @contextlib.contextmanager
    def ocr_lock(self):
        """Only one OCR at a time per source, across processes (the GPU can't run two)."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with open(self.cache_dir / "ocr.lock", "w") as handle:
            filelock.lock(handle)
            try:
                yield
            finally:
                filelock.unlock(handle)

    def _ocr_run(self, first: int, last: int) -> None:
        pages = self._ocr(self.pdf_path, first, last, self.cache_dir / "images")
        self._page_dir.mkdir(parents=True, exist_ok=True)
        for p, (markdown, regions) in self._check_drops(pages).items():
            (self._page_dir / f"{p}.md").write_text(markdown, encoding="utf-8")
            if regions is not None:
                (self._page_dir / f"{p}.check.json").write_text(
                    json.dumps([r.as_json() for r in regions], indent=1),
                    encoding="utf-8",
                )
        for p in range(first, last + 1):
            # A page marker produced no output for is cached empty, so it isn't retried forever.
            if p not in pages:
                (self._page_dir / f"{p}.md").write_text("", encoding="utf-8")

    def _check_drops(
        self, pages: dict[int, str | OcrPage]
    ) -> dict[int, tuple[str, list[Region] | None]]:
        """Find text the OCR dropped on each page (when it gave us its blocks) and put it
        back: re-OCR'd where possible, else from the PDF's text layer."""
        import pymupdf

        result: dict[int, tuple[str, list[Region] | None]] = {
            p: (page, None) for p, page in pages.items() if isinstance(page, str)
        }
        checked = {p: page for p, page in pages.items() if isinstance(page, OcrPage)}
        if not checked:
            return result
        with pymupdf.open(self.pdf_path) as doc:
            drops = {
                p: find_drops(text_lines(doc[p - 1]), page.blocks)
                for p, page in checked.items()
            }
        regions = [(p, r) for p in checked for r in drops[p]]
        recovered = self._reocr(regions)
        for p, page in checked.items():
            mine, recovered = recovered[: len(drops[p])], recovered[len(drops[p]) :]
            result[p] = (repair(page.markdown, page.blocks, drops[p], mine), drops[p])
        return result

    def _reocr(self, regions: list[tuple[int, Region]]) -> list[str]:
        """Re-OCR each region; retry empty results on the region's prose-only crop."""
        ocr_regions = getattr(self._ocr, "ocr_regions", None)
        if not ocr_regions or not regions:
            return [""] * len(regions)
        texts = ocr_regions(self.pdf_path, [(p, r.bbox) for p, r in regions])
        retry = [
            i
            for i, (text, (_, r)) in enumerate(zip(texts, regions))
            if not text.strip() and r.core and r.core != r.bbox
        ]
        if retry:
            again = ocr_regions(
                self.pdf_path, [(regions[i][0], regions[i][1].core) for i in retry]
            )
            for i, text in zip(retry, again):
                texts[i] = text
        return texts

    def _repair_warnings(self, first: int, last: int) -> list[str]:
        warnings = []
        for p in range(first, last + 1):
            check = self._page_dir / f"{p}.check.json"
            if not check.exists():
                continue
            for region in json.loads(check.read_text(encoding="utf-8")):
                if region["repaired_by"] == "text-layer":
                    warnings.append(
                        f"PDF page {p}: OCR dropped a passage; it was restored from the PDF's "
                        "text layer, where math may be garbled. Check it against the book."
                    )
                if not region["placed"]:
                    warnings.append(
                        f"PDF page {p}: a passage the OCR dropped was restored at the end of "
                        "the page because its original position wasn't found."
                    )
        return warnings


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

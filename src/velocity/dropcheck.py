"""Find and repair text that OCR silently dropped.

marker sometimes detects a text block but outputs nothing for it, or misses a block
entirely (M1 findings: 5 passages in 11 pages, one of them the key clause of a
definition). Text comparisons against the PDF's text layer can't catch this reliably, but
positions can: every prose line in the text layer should sit inside a marker block that
produced text. Lines that don't are grouped into regions, which are re-OCR'd on their own
(tightly cropped) and inserted into the page's markdown after the block that precedes them
in marker's reading order. If re-OCR fails too, the text-layer text is used and marked,
since math in the text layer may be garbled.
"""

import html
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass

import pymupdf

# Fonts whose text-layer text is math or symbols, not prose (often with a broken encoding).
_MATH_FONT = re.compile(
    r"math|symbol|dingbat|chemical|pi(lt|std|-|\d|$)|^cm(mi|sy|ex)|euclid|mt(extra|symbol)",
    re.IGNORECASE,
)
# Lines in these blocks may be dropped on purpose (headers) or aren't transcribed (figures).
_SKIP_BLOCKS = {
    "PageHeader",
    "PageFooter",
    "Figure",
    "FigureGroup",
    "Picture",
    "PictureGroup",
    "Diagram",
}
_PAD = 2.0  # points of tolerance when testing whether a line lies inside a block
FALLBACK_NOTE = "*(Recovered from the PDF's text layer; math in it may be garbled.)*"


@dataclass(frozen=True)
class Block:
    """One marker block on a page: its type, bbox in PDF points, and plain-text output."""

    type: str
    bbox: tuple[float, float, float, float]
    text: str


@dataclass(frozen=True)
class Line:
    bbox: tuple[float, float, float, float]
    text: str
    prose: bool = True  # has a prose word; math-only lines (e.g. subscripts) don't


@dataclass
class Region:
    """Dropped lines that belong together, and how they were repaired."""

    bbox: tuple[float, float, float, float]
    text_layer: str  # the lines' text from the PDF text layer
    block: int | None  # index of the empty marker block they were in, if any
    # The prose lines' bbox, before adjacent math lines were taken in. OCR of small crops
    # is sensitive to the exact crop, so this one is tried when the full region gives nothing.
    core: tuple[float, float, float, float] | None = None
    repaired_by: str = ""  # "ocr" or "text-layer"
    placed: bool = (
        False  # inserted after its preceding block (else appended to the page)
    )

    def as_json(self) -> dict:
        return asdict(self)


def block_text(html_text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", html_text or "")).strip()


def text_lines(page: pymupdf.Page) -> list[Line]:
    """Horizontal text-layer lines, each marked as prose if it has a prose word (spans in
    math or symbol fonts don't count)."""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if tuple(line["dir"]) != (1.0, 0.0) or not text:
                continue
            prose = " ".join(
                s["text"] for s in line["spans"] if not _MATH_FONT.search(s["font"])
            )
            lines.append(
                Line(tuple(line["bbox"]), text, bool(re.search(r"[A-Za-z]{2,}", prose)))
            )
    return lines


def _contains(bbox, point) -> bool:
    x, y = point
    return (
        bbox[0] - _PAD <= x <= bbox[2] + _PAD and bbox[1] - _PAD <= y <= bbox[3] + _PAD
    )


def _union(boxes) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def find_drops(lines: list[Line], blocks: list[Block]) -> list[Region]:
    """Prose lines not covered by a block with output, grouped into regions. Each region
    also takes in uncovered math-only lines next to it (a limit's subscript, the end of a
    formula), so a re-OCR of the region sees the whole passage."""
    dropped: list[tuple[Line, int | None]] = []
    for line in lines:
        center = ((line.bbox[0] + line.bbox[2]) / 2, (line.bbox[1] + line.bbox[3]) / 2)
        containing = [i for i, b in enumerate(blocks) if _contains(b.bbox, center)]
        if any(blocks[i].type in _SKIP_BLOCKS for i in containing):
            continue
        if any(blocks[i].text for i in containing):
            continue
        empty = [i for i in containing if blocks[i].type != "Page"]
        dropped.append((line, empty[-1] if empty else None))

    regions: list[Region] = []
    for line, block in dropped:
        if not line.prose:
            continue
        last = regions[-1] if regions else None
        if last and _same_region(last, line, block):
            last.bbox = _union([last.bbox, line.bbox])
            last.text_layer += " " + line.text
        else:
            regions.append(Region(line.bbox, line.text, block))

    math = [line for line, _ in dropped if not line.prose]
    for region in regions:
        region.core = region.bbox
        grew = True
        while grew:
            touching = [m for m in math if _touches(region.bbox, m.bbox)]
            math = [m for m in math if m not in touching]
            for m in touching:
                region.bbox = _union([region.bbox, m.bbox])
                region.text_layer += " " + m.text
            grew = bool(touching)
    return regions


def _touches(a, b, dx: float = 20, dy: float = 3) -> bool:
    """Whether box b lies within dx points beside or dy points above/below box a."""
    return (
        b[0] <= a[2] + dx
        and b[2] >= a[0] - dx
        and b[1] <= a[3] + dy
        and b[3] >= a[1] - dy
    )


def _same_region(region: Region, line: Line, block: int | None) -> bool:
    if region.block is not None or block is not None:
        return region.block == block
    # Lines outside any block: adjacent lines in the same column belong together.
    height = line.bbox[3] - line.bbox[1]
    gap = line.bbox[1] - region.bbox[3]
    overlap = min(region.bbox[2], line.bbox[2]) - max(region.bbox[0], line.bbox[0])
    return -height <= gap <= height and overlap > 0


def preceding_block(region: Region, blocks: list[Block]) -> Block | None:
    """The block with output just before the region in reading order."""
    with_text = [
        (i, b) for i, b in enumerate(blocks) if b.text and b.type not in _SKIP_BLOCKS
    ]
    if region.block is not None:
        before = [b for i, b in with_text if i < region.block]
        return before[-1] if before else None
    # Not in any block (e.g. a margin note): the nearest block starting above it.
    above = [b for _, b in with_text if b.bbox[1] <= region.bbox[1]]
    return max(above, key=lambda b: b.bbox[1]) if above else None


def _normalized(text: str) -> tuple[str, list[int]]:
    """Lowercase letters and digits only, with each one's index in the original text."""
    chars, index = [], []
    for i, c in enumerate(text):
        if c.isalnum():
            chars.append(c.lower())
            index.append(i)
    return "".join(chars), index


def insert_after(markdown: str, anchor: str, text: str) -> tuple[str, bool]:
    """Insert `text` as a paragraph after the paragraph that ends with `anchor`'s text.
    Appends it to the end (returning False) if the anchor isn't found."""
    norm_md, index = _normalized(markdown)
    norm_anchor, _ = _normalized(anchor)
    tail = norm_anchor[-40:]
    pos = norm_md.rfind(tail) if len(tail) >= 8 else -1
    if pos < 0:
        return markdown.rstrip() + "\n\n" + text + "\n", False
    end = index[pos + len(tail) - 1] + 1
    para_end = markdown.find("\n\n", end)
    if para_end < 0:
        return markdown.rstrip() + "\n\n" + text + "\n", True
    return markdown[:para_end] + "\n\n" + text + markdown[para_end:], True


def repair(
    markdown: str,
    blocks: list[Block],
    regions: list[Region],
    recovered: list[str],
) -> str:
    """Insert each region's recovered text (re-OCR output, or "" to use the text layer).

    Regions are inserted last to first: each goes right after its anchor, so regions that
    share an anchor end up in their original order."""
    for region, ocr_text in reversed(list(zip(regions, recovered))):
        if ocr_text.strip():
            # OCR of a small crop can turn a bold label ("1 Definition") into a heading,
            # which read_section would take for a section boundary.
            text = re.sub(
                r"^#{1,6}\s+(.+)$", r"**\1**", ocr_text.strip(), flags=re.MULTILINE
            )
            region.repaired_by = "ocr"
        else:
            text = f"{region.text_layer} {FALLBACK_NOTE}"
            region.repaired_by = "text-layer"
        anchor = preceding_block(region, blocks)
        if anchor is None:
            markdown, region.placed = text + "\n\n" + markdown.lstrip(), True
        else:
            markdown, region.placed = insert_after(markdown, anchor.text, text)
    return markdown


# (pdf path, [(1-indexed page, bbox), ...]) -> OCR'd markdown per region ("" if nothing)
RegionOcr = Callable[..., list[str]]


def crop_pdf(
    src: pymupdf.Document, regions: list[tuple[int, tuple]]
) -> pymupdf.Document:
    """A PDF with one page per region, each a tight crop of it, for re-OCR."""
    out = pymupdf.open()
    for page_number, (x0, y0, x1, y1) in regions:
        # Tight vertically: a few points more and OCR reads the neighbouring line instead.
        clip = pymupdf.Rect(x0 - 6, y0 - 1.5, x1 + 6, y1 + 1.5)
        page = out.new_page(width=clip.width, height=clip.height)
        page.show_pdf_page(page.rect, src, page_number - 1, clip=clip)
    return out

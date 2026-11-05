import json

import pymupdf
import pytest

from velocity.dropcheck import (
    FALLBACK_NOTE,
    Block,
    Line,
    Region,
    crop_pdf,
    find_drops,
    insert_after,
    preceding_block,
    repair,
    text_lines,
)
from velocity.ocr import OcrPage
from velocity.outline import Section
from velocity.pdf_source import PdfSource


def line(y, x0=50, x1=300, text="some prose words"):
    return Line((x0, y, x1, y + 10), text)


def test_text_lines_mark_prose_and_skip_rotated_text():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 100), "A function is continuous here.", fontname="helv")
    page.insert_text((50, 130), "abcdef", fontname="symb")  # Symbol font: math
    page.insert_text((50, 160), "x = 2", fontname="helv")  # no prose word
    page.insert_text((300, 400), "Rotated margin text", fontname="helv", rotate=90)
    assert [(l.text, l.prose) for l in text_lines(page)] == [
        ("A function is continuous here.", True),
        ("abcdef", False),
        ("x = 2", False),
    ]


def test_find_drops():
    blocks = [
        Block("PageHeader", (0, 0, 600, 40), ""),
        Block("Text", (40, 50, 400, 75), "Covered text."),
        Block("Text", (40, 80, 400, 105), ""),  # detected, but no output
        Block("Figure", (40, 300, 400, 400), ""),
    ]
    lines = [
        line(20),  # header: skipped
        line(55),  # covered
        line(82),
        line(94),  # same empty block as the previous line: one region
        line(200),
        line(212),  # no block: adjacent lines group together
        line(260),  # no block, too far away: its own region
        line(350),  # in a figure: labels aren't transcribed, skipped
    ]
    regions = find_drops(lines, blocks)
    assert [(r.block, r.bbox[1], r.bbox[3]) for r in regions] == [
        (2, 82, 104),
        (None, 200, 222),
        (None, 260, 270),
    ]
    assert regions[0].text_layer == "some prose words some prose words"


def test_regions_take_in_adjacent_uncovered_math_lines():
    # Stewart p. 135, exercise 38: "evaluate lim g(x)" with the "x->1" subscript on its
    # own math-only line, and exercise 37's subscript just above (covered by its block).
    blocks = [
        Block("Text", (83, 628, 312, 646), "37. If 4x - 9 <= f(x) ..."),
        Block("Text", (83, 645, 282, 658), ""),
    ]
    lines = [
        Line((83, 645, 282, 658), "38. If 2x < g(x) ... evaluate lim"),
        Line((270, 647, 303, 663), "x -> 1", prose=False),  # subscript: taken in
        Line((278, 639, 292, 646), "x -> 4", prose=False),  # exercise 37's: covered
        Line((400, 700, 420, 710), "y", prose=False),  # far away: not taken in
    ]
    (region,) = find_drops(lines, blocks)
    assert region.bbox == (83, 645, 303, 663)
    assert region.text_layer.endswith("evaluate lim x -> 1")


def test_regions_with_the_same_anchor_keep_their_order():
    # Stewart p. 135: exercises 38 and 39 were both dropped after exercise 37.
    blocks = [
        Block("Text", (40, 50, 400, 60), "37. First exercise."),
        Block("Text", (40, 65, 400, 75), ""),
        Block("Text", (40, 80, 400, 90), ""),
    ]
    regions = [Region((40, 65, 400, 75), "", 1), Region((40, 80, 400, 90), "", 2)]
    out = repair(
        "37. First exercise.\n\n40. Next.", blocks, regions, ["38. A.", "39. B."]
    )
    assert out == "37. First exercise.\n\n38. A.\n\n39. B.\n\n40. Next."


def test_preceding_block_in_reading_order():
    blocks = [
        Block("Text", (40, 50, 400, 75), "First."),
        Block("Equation", (40, 80, 400, 100), "x^2"),
        Block("Text", (40, 110, 400, 130), ""),
        Block("Text", (40, 140, 400, 160), "Later."),
    ]
    assert preceding_block(Region((40, 110, 400, 130), "", 2), blocks).text == "x^2"
    # Outside any block (a margin note at y=85): the nearest block starting above it.
    assert preceding_block(Region((0, 85, 30, 95), "", None), blocks).text == "x^2"
    assert preceding_block(Region((0, 10, 30, 20), "", None), blocks) is None


def test_insert_after_matches_the_anchor_loosely():
    markdown = (
        "Intro.\n\n$$\\lim_{x \\to a} f(x) = -\\infty$$\n\n$$\\text{if } x < N$$\n"
    )
    # The block's text comes from HTML, with different spacing and symbols.
    out, placed = insert_after(
        markdown, "\\lim_{x \\rightarrow a}f(x)=-\\infty", "new text"
    )
    assert not placed  # "rightarrow" vs "to": not the same text
    out, placed = insert_after(
        markdown, "\\lim_{x \\to a}  f(x) = -\\infty", "new text"
    )
    assert placed
    assert out == (
        "Intro.\n\n$$\\lim_{x \\to a} f(x) = -\\infty$$\n\nnew text\n\n$$\\text{if } x < N$$\n"
    )
    out, placed = insert_after("Only text.", "missing anchor text", "new")
    assert (out, placed) == ("Only text.\n\nnew\n", False)


def test_repair_uses_ocr_text_or_the_text_layer():
    blocks = [
        Block("Text", (40, 50, 400, 75), "The paragraph before."),
        Block("Text", (40, 80, 400, 100), ""),
    ]
    markdown = "The paragraph before.\n\nThe paragraph after."
    regions = [Region((40, 80, 400, 100), "garbled fsxd text", 1)]
    out = repair(markdown, blocks, regions, ["# 1 Definition\n\nA function $f$ is"])
    assert out == (
        "The paragraph before.\n\n**1 Definition**\n\nA function $f$ is\n\nThe paragraph after."
    )
    assert (regions[0].repaired_by, regions[0].placed) == ("ocr", True)

    regions = [Region((40, 80, 400, 100), "garbled fsxd text", 1)]
    out = repair(markdown, blocks, regions, [""])
    assert f"garbled fsxd text {FALLBACK_NOTE}" in out
    assert regions[0].repaired_by == "text-layer"


def test_crop_pdf_makes_one_tight_page_per_region():
    src = pymupdf.open()
    src.new_page().insert_text((50, 100), "Dropped line.", fontname="helv")
    crops = crop_pdf(src, [(1, (50, 90, 150, 102))])
    assert len(crops) == 1
    assert crops[0].rect.height == pytest.approx(12 + 3)
    assert "Dropped line." in crops[0].get_text()


# -- in a PDF source --------------------------------------------------------------------


@pytest.fixture
def pdf(tmp_path):
    """Page 1: two paragraphs; page 2: one paragraph."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 100), "Kept paragraph on the first page.", fontname="helv")
    page.insert_text((50, 130), "Dropped paragraph on the first page.", fontname="helv")
    doc.new_page().insert_text((50, 100), "Second page text.", fontname="helv")
    path = tmp_path / "book.pdf"
    doc.save(path)
    return path


OUTLINE = [Section("1", "1: Only", 1, None, 1, 2)]


class BlockOcr:
    """Marker-like OCR whose block for the second paragraph has no output."""

    def __init__(self, regions_ok=True):
        self.region_calls = []
        self.regions_ok = regions_ok

    def __call__(self, pdf_path, first, last, image_dir):
        pages = {
            1: OcrPage(
                "## 1 Only\n\nKept paragraph on the first page.",
                [
                    Block(
                        "Text", (45, 85, 400, 105), "Kept paragraph on the first page."
                    ),
                    Block("Text", (45, 115, 400, 135), ""),
                ],
            ),
            2: OcrPage(
                "Second page text.",
                [Block("Text", (45, 85, 400, 105), "Second page text.")],
            ),
        }
        return {p: pages[p] for p in range(first, last + 1)}

    def ocr_regions(self, pdf_path, regions):
        self.region_calls.append(regions)
        return [
            "Dropped paragraph on the first page." if self.regions_ok else ""
        ] * len(regions)


def test_source_repairs_dropped_text_and_records_the_check(tmp_path, pdf):
    ocr = BlockOcr()
    source = PdfSource(pdf, ocr, cache_dir=tmp_path / "cache", outline=OUTLINE)
    section = source.read_section("1")
    assert (
        "Kept paragraph on the first page.\n\nDropped paragraph on the first page."
        in section.markdown
    )
    assert section.warnings == []
    assert (
        len(ocr.region_calls) == 1 and ocr.region_calls[0][0][0] == 1
    )  # one batch, page 1
    check = json.loads((tmp_path / "cache/pages/1.check.json").read_text())
    assert [(r["repaired_by"], r["placed"]) for r in check] == [("ocr", True)]
    assert json.loads((tmp_path / "cache/pages/2.check.json").read_text()) == []


def test_text_layer_fallback_is_flagged_to_the_tutor(tmp_path, pdf):
    source = PdfSource(
        pdf, BlockOcr(regions_ok=False), cache_dir=tmp_path / "c", outline=OUTLINE
    )
    section = source.read_section("1")
    assert f"Dropped paragraph on the first page. {FALLBACK_NOTE}" in section.markdown
    assert len(section.warnings) == 1 and "text layer" in section.warnings[0]


def test_recheck_reocrs_pages_cached_before_the_check(tmp_path, pdf):
    cache = tmp_path / "cache"
    (cache / "pages").mkdir(parents=True)
    for p in (1, 2):
        (cache / "pages" / f"{p}.md").write_text("old OCR without the check")
    source = PdfSource(pdf, BlockOcr(), cache_dir=cache, outline=OUTLINE)
    assert source.unchecked_pages() == [1, 2]
    assert source.recheck() == [1, 2]
    assert source.unchecked_pages() == []
    assert "Dropped paragraph" in (cache / "pages/1.md").read_text()
    (cache / "pages/1.md").write_text("changed")
    assert source.recheck() == []  # all checked now
    assert source.recheck(all_pages=True) == [1, 2]
    assert "Dropped paragraph" in (cache / "pages/1.md").read_text()


class CropSensitiveOcr:
    """Like marker on Stewart p. 135: nothing for a crop that includes the subscript line,
    the full text for the tight prose-only crop."""

    def __init__(self):
        self.calls = []

    def ocr_regions(self, pdf_path, regions):
        self.calls.append([bbox for _, bbox in regions])
        return [
            "" if bbox[3] > 681 else "Find the value of $c$." for _, bbox in regions
        ]


def test_empty_reocr_is_retried_on_the_prose_only_crop(tmp_path, pdf):
    ocr = CropSensitiveOcr()
    source = PdfSource(pdf, ocr, cache_dir=tmp_path, outline=OUTLINE)
    grown = Region(
        (350, 669, 505, 684), "Find the value of c", 1, core=(350, 669, 505, 680)
    )
    plain = Region((50, 100, 300, 110), "Another", 2, core=(50, 100, 300, 110))
    assert source._reocr([(135, grown), (135, plain)]) == [
        "Find the value of $c$.",
        "Find the value of $c$.",
    ]
    # One call for both regions, then a retry for the grown one only.
    assert ocr.calls == [[grown.bbox, plain.bbox], [grown.core]]

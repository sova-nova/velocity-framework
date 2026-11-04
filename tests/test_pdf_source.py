import os
from pathlib import Path

import pytest

from tutor_harness.ocr import split_paginated
from tutor_harness.outline import Section
from tutor_harness.pdf_source import PdfSource

OUTLINE = [
    Section("ch2", "Ch 2: Limits and Derivatives", 1, None, 10, 13),
    Section("2.4", "2.4: The Precise Definition of a Limit", 2, "ch2", 11, 12),
    Section("2.5", "2.5: Continuity", 2, "ch2", 12, 13),
]

# Page 11 ends section 2.3's exercises; 2.4 starts mid-page, has an EXERCISES heading,
# and 2.5 starts mid-page 12.
PAGES = {
    10: "# 2 Limits and Derivatives\n\nChapter intro.\n\n## 2.1 The Tangent Problem",
    11: "**66.** Last exercise of 2.3.\n\n## 2.4 The Precise Definition of a Limit\n\nLet $f$ be a function.",
    12: "## 2.4 EXERCISES\n\n**1.** Prove it.\n\n## 2.5 Continuity\n\nA function is continuous.",
    13: "More on continuity.",
}


class FakeOcr:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def __call__(self, pdf_path, first_page, last_page, image_dir):
        self.calls.append((first_page, last_page))
        return {p: PAGES[p] for p in range(first_page, last_page + 1)}


@pytest.fixture
def ocr() -> FakeOcr:
    return FakeOcr()


@pytest.fixture
def source(tmp_path, ocr) -> PdfSource:
    return PdfSource("book.pdf", ocr, cache_dir=tmp_path, outline=OUTLINE)


def test_section_runs_from_its_heading_to_the_next_entry(source):
    text = source.read_section("2.4")
    assert text.markdown == (
        "## 2.4 The Precise Definition of a Limit\n\nLet $f$ be a function."
        "\n\n## 2.4 EXERCISES\n\n**1.** Prove it."
    )
    assert (text.first_page, text.last_page, text.warnings) == (11, 12, [])


def test_last_section_runs_to_end_of_book(source):
    text = source.read_section("2.5")
    assert text.markdown.startswith("## 2.5 Continuity")
    assert text.markdown.endswith("More on continuity.")
    assert text.warnings == []


def test_parent_section_returns_only_its_intro(source):
    text = source.read_section("ch2")
    assert "Chapter intro." in text.markdown
    assert "Let $f$" not in text.markdown


def test_pages_are_ocred_once_and_shared_between_sections(source, ocr):
    source.read_section("2.4")
    source.read_section("2.5")
    source.read_section("2.4")
    assert ocr.calls == [(11, 12), (13, 13)]


def test_cache_survives_a_new_source_instance(tmp_path, ocr):
    PdfSource("book.pdf", ocr, cache_dir=tmp_path, outline=OUTLINE).read_section("2.4")
    PdfSource("book.pdf", ocr, cache_dir=tmp_path, outline=OUTLINE).read_section("2.4")
    assert ocr.calls == [(11, 12)]


def test_missing_headings_produce_warnings_not_silence(tmp_path):
    def ocr_without_headings(pdf_path, first_page, last_page, image_dir):
        return {p: f"page {p}" for p in range(first_page, last_page + 1)}

    src = PdfSource(
        "book.pdf", ocr_without_headings, cache_dir=tmp_path, outline=OUTLINE
    )
    text = src.read_section("2.4")
    assert text.markdown == "page 11\n\npage 12"
    assert len(text.warnings) == 2


def test_unknown_section_id(source):
    with pytest.raises(KeyError, match="9.9"):
        source.read_section("9.9")


def test_split_paginated_uses_one_indexed_pages():
    md = "{135}" + "-" * 48 + "\n\nfirst\n\n{136}" + "-" * 48 + "\n\nsecond\n"
    assert split_paginated(md) == {136: "first", 137: "second"}


STEWART = next(Path("sources/mathematics").glob("*.pdf"), None)


@pytest.mark.skipif(
    STEWART is None or not os.environ.get("VELOCITY_OCR_TESTS"),
    reason="slow: needs the test book, the ocr group, and VELOCITY_OCR_TESTS=1",
)
def test_real_ocr_of_section_2_4(tmp_path):
    from tutor_harness.ocr import MarkerOcr

    text = PdfSource(STEWART, MarkerOcr(), cache_dir=tmp_path).read_section("2.4")
    assert text.warnings == []
    assert text.markdown.startswith("## 2.4 The Precise Definition of a Limit")
    assert r"\lim_{x \rightarrow a} f(x) = L" in text.markdown
    assert "## 2.5" not in text.markdown
    assert "2.3" not in text.markdown.splitlines()[0]

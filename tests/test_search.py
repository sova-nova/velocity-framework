"""Keyword search over sources (search.py, PdfSource/TextSource.search_chunks, and the
search_source tool)."""

import json

import pymupdf
import pytest

from velocity.config import Config, SourceRef, Subject
from velocity.outline import Section
from velocity.pdf_source import PdfSource
from velocity.search import Chunk, format_hits, normalize, search, variants
from velocity.text_source import TextSource
from velocity.tools import ToolError, TutorTools

# -- matching ----------------------------------------------------------------------------


def test_variants_cover_text_layers_that_lose_a_ligatures_f():
    # As seen in Stewart's text layer.
    assert "deinition" in variants("definition")
    assert "lux" in variants("flux")
    assert {"suficient", "suicient"} <= variants("sufficient")
    assert "diferentiation" in variants("differentiation")
    assert variants("limit") == {"limit"}
    assert variants("fi") == {"fi"}  # too short to strip


def test_normalize_spells_out_ligature_characters_and_joins_broken_words():
    assert normalize("The deﬁnition of conti-\nnuity’s  Limit") == (
        "the definition of continuity's limit"
    )


def chunk(section, text, page=1, title=None):
    return Chunk(section, title or f"Section {section}", page, text)


def test_sections_with_every_word_rank_first():
    hits = search(
        [
            chunk("2.3", "The squeeze theorem says ...", 10),
            chunk("2.3", "More on the squeeze theorem.", 11),
            chunk("2.4", "A theorem about limits.", 12),
            chunk("5.1", "Squeeze the lemon.", 40),
        ],
        "Squeeze Theorem",
    )
    assert [h.section_id for h in hits] == ["2.3"]
    assert hits[0].pages == [10, 11] and hits[0].complete


def test_the_best_page_is_listed_first():
    hits = search(
        [
            chunk("2.3", "a theorem", 9),
            chunk("2.3", "the squeeze theorem", 10),
            chunk("2.3", "squeeze", 12),
        ],
        "squeeze theorem",
    )
    assert hits[0].pages == [10, 9, 12]
    assert "(best match PDF p. 10; also on pp. 9, 12)" in format_hits(
        "Book", "squeeze theorem", hits
    )
    assert "squeeze theorem" in hits[0].snippet


def test_partial_matches_when_no_section_has_every_word():
    hits = search(
        [chunk("1", "limits and more limits"), chunk("2", "a word on continuity")],
        "limits continuity epsilon",
    )
    assert {h.section_id for h in hits} == {"1", "2"}
    assert not hits[0].complete
    assert format_hits("Book", "q", hits).startswith("Partial matches")


def test_an_index_page_does_not_outrank_the_section_that_teaches_it():
    index = "integral, 3 integral, 5 " * 40 + "flux integral, 1129"
    hits = search(
        [
            chunk("index", index, 1300, "Index"),
            chunk("16.7", "the surface integral (or lux integral) of F over S", 1154),
            chunk("16.7", "We compute the flux of a field.", 1155),
        ],
        "flux integral",
    )
    assert hits[0].section_id == "16.7"


def test_a_section_named_after_the_query_ranks_first():
    hits = search(
        [
            chunk("2.2", "a definition of a limit " * 3, 5, "2.2: The Limit"),
            chunk("2.4", "by the definition", 9, "2.4: The Precise Definition"),
        ],
        "precise definition",
    )
    # Neither page says "precise", but 2.4 is named after the query.
    assert hits[0].section_id == "2.4"


def test_format_hits():
    hits = search([chunk("2.4", "the precise definition", page=137)], "definition")
    text = format_hits("Stewart", "definition", hits)
    assert "- Stewart §2.4: Section 2.4 (best match PDF p. 137): the precise" in text
    assert "math in them may be garbled" in text
    assert "No matches" in format_hits("Stewart", "zebra", [])


# -- sources -----------------------------------------------------------------------------

OUTLINE = [
    Section("ch1", "Ch 1: Limits", 1, None, 2, 4),
    Section("1.1", "1.1: The Squeeze Theorem", 2, "ch1", 3, 3),
    Section("1.2", "1.2: Continuity", 2, "ch1", 4, 4),
]


@pytest.fixture
def book(tmp_path):
    path = tmp_path / "book.pdf"
    doc = pymupdf.open()
    pages = [
        "Preface: the squeeze theorem is in 1.1.",
        "Chapter one introduces limits.",
        "The squeeze theorem says that ...",
        "",  # a scanned page: no text layer
    ]
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(path)
    return path


def test_pdf_search_uses_the_text_layer_and_cached_ocr_for_scanned_pages(
    tmp_path, book
):
    pdf = PdfSource(book, ocr=None, cache_dir=tmp_path / "cache", outline=OUTLINE)
    (tmp_path / "cache/pages").mkdir(parents=True)
    (tmp_path / "cache/pages/4.md").write_text("A function is continuous if ...")
    chunks = pdf.search_chunks()
    # Page 1 is before the outline (front matter), so it isn't searched.
    assert [(c.section_id, c.page) for c in chunks] == [
        ("ch1", 2),
        ("1.1", 3),
        ("1.2", 4),
    ]
    assert "continuous" in chunks[-1].text  # page 4 has no text layer: OCR cache
    cached = json.loads((tmp_path / "cache/text-layer.json").read_text())
    assert cached[2].startswith("The squeeze theorem")

    # A new instance reads the cached text layer instead of the PDF.
    book.write_bytes(b"not a pdf anymore")
    again = PdfSource(book, ocr=None, cache_dir=tmp_path / "cache", outline=OUTLINE)
    assert len(again.search_chunks()) == 3


def test_text_source_search_chunks_are_its_sections(tmp_path):
    md = tmp_path / "notes.md"
    md.write_text("# Limits\n\nThe squeeze theorem.\n\n# Continuity\n\nNo gaps.\n")
    chunks = TextSource(md).search_chunks()
    assert [(c.section_id, c.page) for c in chunks] == [
        ("limits", 0),
        ("continuity", 0),
    ]
    assert "squeeze" in chunks[0].text and "squeeze" not in chunks[1].text


def test_search_source_tool_searches_every_source(tmp_path, book, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    md = tmp_path / "notes.md"
    md.write_text("# Limits\n\nThe squeeze theorem, in my words.\n")
    pymupdf_doc = pymupdf.open(book)
    pymupdf_doc.set_toc([[1, "Ch 1: Limits", 2], [2, "1.1: The Squeeze Theorem", 3]])
    pymupdf_doc.saveIncr()
    subject = Subject(
        "calculus",
        "Calculus",
        tmp_path / "vault/Tutor/calculus",
        "",
        (),
        (SourceRef(book, "Book"), SourceRef(md, "Notes")),
    )
    tools = TutorTools(Config(tmp_path / "vault"), subject)

    both = tools.search_source("squeeze theorem")
    assert "- Book §1.1: 1.1: The Squeeze Theorem (best match PDF p. 3)" in both
    assert "- Notes §limits: Limits: " in both
    assert "Notes" not in tools.search_source("squeeze", source="Book")
    assert "No matches for 'zebra' in Book" in tools.search_source("zebra", "Book")
    with pytest.raises(ToolError, match="Give some words"):
        tools.search_source("  ")
    with pytest.raises(ToolError, match="Unknown source"):
        tools.search_source("limits", source="Nope")

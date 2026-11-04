from pathlib import Path

import pytest

from tutor_harness.outline import Section, build_outline, get_outline

TOC = [
    (1, "Preface", 13),
    (1, "Ch 1: Functions and Models", 41),
    (2, "1.1: Four Ways to Represent a Function", 42),
    (2, "Review", 100),
    (2, "Principles of Problem Solving", 103),
    (1, "Ch 2: Limits and Derivatives", 109),
    (2, "2.4: The Precise \x00Definition of a Limit", 136),
    (2, "2.5: Continuity", 146),
    (2, "Review", 197),
    (2, "Problems Plus", 201),
    (1, "Appendixes", 1215),
    (2, "Appendix F: Proofs of Theorems", 1253),
    (1, "Index", 1353),
]


@pytest.fixture
def outline() -> dict[str, Section]:
    return {s.id: s for s in build_outline(TOC, page_count=1397)}


def test_ids(outline):
    assert list(outline) == [
        "preface",
        "ch1",
        "1.1",
        "1.review",
        "1.principles-of-problem-solving",
        "ch2",
        "2.4",
        "2.5",
        "2.review",
        "2.problems-plus",
        "appendixes",
        "app-f",
        "index",
    ]


def test_title_strips_nul_bytes(outline):
    assert outline["2.4"].title == "2.4: The Precise Definition of a Limit"


def test_parents(outline):
    assert outline["2.4"].parent_id == "ch2"
    assert outline["app-f"].parent_id == "appendixes"
    assert outline["ch2"].parent_id is None


def test_sibling_sections_share_boundary_page(outline):
    assert (outline["2.4"].start_page, outline["2.4"].end_page) == (136, 146)


def test_last_section_of_chapter_ends_before_next_chapter(outline):
    assert outline["1.principles-of-problem-solving"].end_page == 108
    assert outline["2.problems-plus"].end_page == 1214


def test_chapter_spans_until_next_top_level_entry(outline):
    assert (outline["ch2"].start_page, outline["ch2"].end_page) == (109, 1214)
    assert (outline["preface"].start_page, outline["preface"].end_page) == (13, 40)


def test_last_entry_runs_to_end_of_book(outline):
    assert outline["index"].end_page == 1397


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        build_outline(
            [(1, "Ch 1: A", 1), (2, "Review", 2), (2, "Review", 3)], page_count=5
        )


STEWART = next(Path("sources/mathematics").glob("*.pdf"), None)


@pytest.mark.skipif(STEWART is None, reason="test textbook not present")
def test_real_book_outline():
    sections = {s.id: s for s in get_outline(STEWART)}
    assert len(sections) == 188
    assert sections["2.4"].title == "2.4: The Precise Definition of a Limit"
    assert (sections["2.4"].start_page, sections["2.4"].end_page) == (136, 146)
    assert sections["16.10"].parent_id == "ch16"

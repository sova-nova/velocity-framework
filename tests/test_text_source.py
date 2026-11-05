import zipfile

import pytest

from velocity.config import Config, SourceRef, Subject
from velocity.text_source import TextSource, html_to_markdown
from velocity.tools import ToolError, TutorTools

MARKDOWN = """\
# Chapter 1 Limits

Intro to limits.

## 1.1 The idea of a limit

A limit is what $f(x)$ approaches.

```python
# not a heading
```

## Exercises

1. Find $\\lim_{x \\to 2} x^2$.

Chapter 2 Derivatives
=====================

Slopes.

Worked example
--------------

## Exercises

1. Differentiate $x^3$.
"""


def test_markdown_outline_and_sections(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text(MARKDOWN)
    source = TextSource(path)
    outline = [(s.id, s.title, s.level, s.parent_id) for s in source.outline()]
    assert outline == [
        ("ch1", "Chapter 1 Limits", 1, None),
        ("1.1", "1.1 The idea of a limit", 2, "ch1"),
        ("exercises", "Exercises", 2, "ch1"),
        ("ch2", "Chapter 2 Derivatives", 1, None),
        ("worked-example", "Worked example", 2, "ch2"),
        ("exercises-2", "Exercises", 2, "ch2"),
    ]
    section = source.read_section("1.1")
    assert section.markdown.startswith("## 1.1 The idea of a limit")
    assert "# not a heading" in section.markdown  # code stays in, and isn't a heading
    assert "Exercises" not in section.markdown
    assert (
        source.read_section("ch1").markdown == "# Chapter 1 Limits\n\nIntro to limits."
    )
    assert source.read_section("exercises-2").markdown.endswith("Differentiate $x^3$.")
    with pytest.raises(KeyError):
        source.read_section("9.9")


def test_html_to_markdown():
    html = """<html><head><title>T</title><style>p{}</style></head><body>
      <h1 id="top">Limits &amp; continuity</h1>
      <p>A <strong>limit</strong> is <em>what</em> f approaches.<br>New line.</p>
      <ul><li>one</li><li>two <code>x</code></li></ul>
      <ol><li>first</li><li>second</li></ol>
      <pre>def f(x):
    return x</pre>
      <p>Inline <math alttext="x^2"><mi>x</mi><mn>2</mn></math> and
        <math display="block"><semantics><mrow><mi>y</mi></mrow>
        <annotation encoding="application/x-tex">\\frac{1}{y}</annotation></semantics></math></p>
      <script>alert(1)</script>
      <img src="a.png" alt="graph of f">
      <p>So <img alt="y equals x squared" data-tex="\\({y=x^{2}}\\)" src="1.svg"/> and
        <img alt="sum" data-tex="\\[\\sum_i x_i\\]" src="2.svg"/></p>
    </body></html>"""
    md = html_to_markdown(html).replace("\x00#\x00", "").replace("\x00#top\x00", "")
    assert "# Limits & continuity" in md
    assert "A **limit** is *what* f approaches.\nNew line." in md
    assert "- one" in md and "- two `x`" in md
    assert "1. first" in md and "2. second" in md
    assert "```\ndef f(x):\n    return x\n```" in md
    assert "Inline $x^2$ and" in md
    assert "$$\\frac{1}{y}$$" in md
    assert "alert" not in md and "p{}" not in md and "T\n" not in md
    assert "[image: graph of f]" in md
    # Math images with their LaTeX in data-tex (as in Project Gutenberg's EPUBs).
    assert "So ${y=x^{2}}$ and" in md and "$$\\sum_i x_i$$" in md


def test_html_source_uses_headings(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(
        "<h1>Chapter 3 Integrals</h1><p>Area.</p><h2>3.1 Riemann sums</h2><p>Sums.</p>"
    )
    source = TextSource(path)
    assert [s.id for s in source.outline()] == ["ch3", "3.1"]
    text = source.read_section("3.1").markdown
    assert text == "## 3.1 Riemann sums\n\nSums."
    assert "\x00" not in source.read_section("ch3").markdown


CHAPTER = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>{}</body></html>"""


def make_epub(path, nav=True, ncx=False):
    files = {
        "META-INF/container.xml": """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles></container>""",
        "OEBPS/content.opf": f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    {'<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>' if nav else ""}
    {'<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>' if ncx else ""}
    <item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine {'toc="ncx"' if ncx else ""}><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>""",
        "OEBPS/text/ch1.xhtml": CHAPTER.format(
            '<h1>Chapter 1</h1><p>Opening.</p><h2 id="s2">Second part</h2><p>More.</p>'
        ),
        "OEBPS/text/ch2.xhtml": CHAPTER.format("<h1>Chapter 2</h1><p>The end.</p>"),
    }
    if nav:
        files["OEBPS/nav.xhtml"] = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>
<nav epub:type="toc"><ol>
  <li><a href="text/ch1.xhtml">One: Beginnings</a>
    <ol><li><a href="text/ch1.xhtml#s2">1.2 The second part</a></li></ol></li>
  <li><a href="text/ch2.xhtml">Two: Endings</a></li>
</ol></nav></body></html>"""
    if ncx:
        files["OEBPS/toc.ncx"] = """<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
  <navPoint><navLabel><text>One: Beginnings</text></navLabel><content src="text/ch1.xhtml"/>
    <navPoint><navLabel><text>1.2 The second part</text></navLabel>
      <content src="text/ch1.xhtml#s2"/></navPoint></navPoint>
  <navPoint><navLabel><text>Two: Endings</text></navLabel><content src="text/ch2.xhtml"/></navPoint>
</navMap></ncx>"""
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


@pytest.mark.parametrize("nav, ncx", [(True, False), (False, True)])
def test_epub_uses_the_books_table_of_contents(tmp_path, nav, ncx):
    source = TextSource(make_epub(tmp_path / "book.epub", nav=nav, ncx=ncx))
    outline = [(s.id, s.title, s.level) for s in source.outline()]
    assert outline == [
        ("one-beginnings", "One: Beginnings", 1),
        ("1.2", "1.2 The second part", 2),
        ("two-endings", "Two: Endings", 1),
    ]
    assert source.read_section("one-beginnings").markdown == "# Chapter 1\n\nOpening."
    assert source.read_section("1.2").markdown == "## Second part\n\nMore."
    assert source.read_section("two-endings").markdown == "# Chapter 2\n\nThe end."


def test_epub_without_a_table_of_contents_uses_headings(tmp_path):
    source = TextSource(make_epub(tmp_path / "book.epub", nav=False))
    assert [s.title for s in source.outline()] == [
        "Chapter 1",
        "Second part",
        "Chapter 2",
    ]


def test_tools_read_text_sources(tmp_path):
    (tmp_path / "vault").mkdir()
    md = tmp_path / "notes.md"
    md.write_text(MARKDOWN)
    other = tmp_path / "data.csv"
    other.write_text("a,b")
    config = Config(tmp_path / "vault")
    subject = Subject(
        "calculus",
        "Calculus",
        config.tutor_dir / "calculus",
        "",
        (),
        (SourceRef(md, "Notes"), SourceRef(other, "Data")),
    )
    tools = TutorTools(config, subject)
    outline = tools.get_outline("Notes")
    assert "1.1 The idea of a limit [id: 1.1]" in outline and "PDF" not in outline
    section = tools.read_section("Notes", "1.1")
    assert section.startswith("Notes §1.1: 1.1 The idea of a limit\n\n## 1.1")
    assert "Find $\\lim_{x \\to 2} x^2$" in tools.get_exercises("Notes", "exercises")
    with pytest.raises(ToolError, match="unsupported file type"):
        tools.get_outline("Data")


def test_section_ids_for_roman_numeral_chapters(tmp_path):
    path = tmp_path / "old.md"
    path.write_text("# CHAPTER IV. SIMPLEST CASES.\n\nText.\n\n# CHAPTER XIX. AREAS.\n")
    assert [s.id for s in TextSource(path).outline()] == ["ch4", "ch19"]


def test_exercises_after_a_label_paragraph(tmp_path):
    (tmp_path / "vault").mkdir()
    md = tmp_path / "old.md"
    md.write_text(
        "# CHAPTER IV. CASES.\n\nTheory.\n\n*Exercises I.* (See p. 252.)\n\n(1) $y=x^{13}$\n"
    )
    config = Config(tmp_path / "vault")
    subject = Subject(
        "c", "C", config.tutor_dir / "c", "", (), (SourceRef(md, "Easy"),)
    )
    exercises = TutorTools(config, subject).get_exercises("Easy", "ch4")
    assert "*Exercises I.*" in exercises and "(1) $y=x^{13}$" in exercises
    assert "Theory" not in exercises

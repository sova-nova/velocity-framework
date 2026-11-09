"""Markdown, HTML and EPUB sources: the file becomes markdown, and its outline comes from
its headings (or, for EPUB, the book's own table of contents).

Only the standard library is used. Math written as LaTeX passes through as is; MathML is
replaced by its LaTeX annotation or alt text when it has one.
"""

import html
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

from velocity.outline import Section
from velocity.pdf_source import SectionText
from velocity.search import Chunk

TEXT_SUFFIXES = {".md", ".markdown", ".html", ".htm", ".xhtml", ".epub"}

# Marks an element id in converted text: \x00<doc>#<id>\x00, removed before text is returned.
_ANCHOR = re.compile(r"\x00([^\x00]*)\x00")


@dataclass(frozen=True)
class Entry:
    level: int
    title: str
    offset: int  # where the section starts in the document text


class TextSource:
    """A source whose text is available directly (no OCR)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._loaded: tuple[str, list[Entry]] | None = None
        self._outline: list[Section] | None = None

    def _load(self) -> tuple[str, list[Entry]]:
        if self._loaded is None:
            suffix = self.path.suffix.lower()
            if suffix == ".epub":
                text, entries = read_epub(self.path)
            else:
                raw = self.path.read_text(encoding="utf-8", errors="replace")
                text = raw if suffix in (".md", ".markdown") else html_to_markdown(raw)
                text = _ANCHOR.sub("", text)
                entries = heading_entries(text)
            self._loaded = (text, entries)
        return self._loaded

    def outline(self) -> list[Section]:
        if self._outline is None:
            _, entries = self._load()
            self._outline = build_text_outline(entries)
        return self._outline

    def read_section(self, section_id: str) -> SectionText:
        """A section's text, from its start to the start of the next outline entry."""
        text, entries = self._load()
        outline = self.outline()
        index = next((i for i, s in enumerate(outline) if s.id == section_id), None)
        if index is None:
            raise KeyError(f"unknown section id {section_id!r}")
        start = entries[index].offset
        end = entries[index + 1].offset if index + 1 < len(entries) else len(text)
        section = outline[index]
        return SectionText(section.id, section.title, 0, 0, text[start:end].strip())

    def search_chunks(self) -> list[Chunk]:
        """Each section's own text (up to the next outline entry), for search."""
        text, entries = self._load()
        ends = [e.offset for e in entries[1:]] + [len(text)]
        return [
            Chunk(s.id, s.title, 0, text[e.offset : end])
            for s, e, end in zip(self.outline(), entries, ends)
        ]


# -- outline -----------------------------------------------------------------------------

_ATX = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_SETEXT = re.compile(r"^(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^(```|~~~)")


def heading_entries(markdown: str) -> list[Entry]:
    """ATX (# Title) and setext (Title / ===) headings, outside fenced code blocks."""
    entries = []
    offset = 0
    in_fence = False
    lines = markdown.splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if _FENCE.match(stripped):
            in_fence = not in_fence
        elif not in_fence:
            if m := _ATX.match(stripped):
                entries.append(Entry(len(m[1]), _clean_title(m[2]), offset))
            elif (
                stripped.strip()
                and i + 1 < len(lines)
                and _SETEXT.match(lines[i + 1].rstrip("\n"))
                and not _ATX.match(stripped)
            ):
                level = 1 if lines[i + 1].startswith("=") else 2
                entries.append(Entry(level, _clean_title(stripped), offset))
        offset += len(line)
    return entries


def _clean_title(title: str) -> str:
    title = re.sub(r"[*_`]+", "", title)
    return " ".join(html.unescape(title).split())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "section"


_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def _roman(numeral: str) -> int:
    values = [_ROMAN[c] for c in numeral.lower()]
    return sum(-v if v < after else v for v, after in zip(values, values[1:] + [0]))


def _base_id(title: str) -> str:
    if m := re.match(r"(?:chapter|ch\.?)\s+(\d+)\b", title, re.IGNORECASE):
        return f"ch{m[1]}"
    if m := re.match(r"chapter\s+([IVXLC]+)\b", title, re.IGNORECASE):
        return f"ch{_roman(m[1])}"
    if m := re.match(r"(?:part)\s+(\d+|[IVXLC]+)\b", title, re.IGNORECASE):
        return f"part-{m[1].lower()}"
    if m := re.match(r"(?:appendix)\s+([A-Z0-9]+)\b", title, re.IGNORECASE):
        return f"app-{m[1].lower()}"
    if m := re.match(r"(\d+(?:\.\d+)+|\d+)[.:)]?\s", title + " "):
        return m[1]
    return _slug(title)


def build_text_outline(entries: list[Entry]) -> list[Section]:
    """Sections with stable ids: numbers for numbered titles ("2.4 ..."), ch<n> for
    chapters, otherwise a slug of the title; repeats get -2, -3, ..."""
    sections: list[Section] = []
    used: dict[str, int] = {}
    parents: dict[int, str] = {}
    for entry in entries:
        base = _base_id(entry.title)
        used[base] = used.get(base, 0) + 1
        sid = base if used[base] == 1 else f"{base}-{used[base]}"
        parent = next(
            (parents[lv] for lv in range(entry.level - 1, 0, -1) if lv in parents), None
        )
        parents[entry.level] = sid
        for deeper in [lv for lv in parents if lv > entry.level]:
            del parents[deeper]
        sections.append(Section(sid, entry.title, entry.level, parent, 0, 0))
    return sections


# -- HTML to markdown --------------------------------------------------------------------

_BLOCK = {
    "p", "div", "section", "article", "main", "header", "footer", "aside", "blockquote",
    "ul", "ol", "li", "table", "tr", "pre", "figure", "figcaption", "dl", "dt", "dd",
    "hr", "body", "nav", "h1", "h2", "h3", "h4", "h5", "h6",
}  # fmt: skip
_SKIP = {"script", "style", "head", "title", "template", "svg"}
_INLINE_MARK = {"strong": "**", "b": "**", "em": "*", "i": "*"}


class _Converter(HTMLParser):
    def __init__(self, doc: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.doc = doc
        self.out: list[str] = []
        self.skip = 0
        self.pre = 0
        self.lists: list[list] = []  # [tag, item count]
        self.math: list | None = None  # [display, alt, tex, text parts, depth]
        self.cell = False

    # helpers
    def _text(self, s: str) -> None:
        if self.skip:
            return
        if self.math is not None:
            self.math[3].append(s)
            return
        if not self.pre:
            s = re.sub(r"\s+", " ", s)
            if self._at_line_start():
                s = s.lstrip()
        self.out.append(s)

    def _tail(self) -> str:
        """The end of the output so far, without anchor markers."""
        return _ANCHOR.sub("", "".join(self.out[-8:]))

    def _at_line_start(self) -> bool:
        tail = self._tail()
        return not tail or tail.endswith("\n")

    def _block(self) -> None:
        tail = self._tail()
        if tail and not tail.endswith("\n\n"):
            self.out.append("\n" if tail.endswith("\n") else "\n\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if self.math is not None:
            self.math[4] += 1
            if tag == "annotation" and "tex" in (a.get("encoding") or "").lower():
                self.math[2] = ""  # collect the annotation's text as the LaTeX
                self.math[3] = []
            return
        if tag in _SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if a.get("id"):
            self.out.append(f"\x00{self.doc}#{a['id']}\x00")
        if tag == "math":
            display = a.get("display") == "block"
            self.math = [display, a.get("alttext"), None, [], 0]
            return
        if tag in _BLOCK:
            self._block()
        if m := re.fullmatch(r"h([1-6])", tag):
            self.out.append("#" * int(m[1]) + " ")
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
        elif tag == "li":
            indent = "  " * max(0, len(self.lists) - 1)
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] += 1
            self.out.append(indent + (f"{kind[1]}. " if kind[0] == "ol" else "- "))
        elif tag == "pre":
            self.pre += 1
            self.out.append("```\n")
        elif tag == "code" and not self.pre:
            self.out.append("`")
        elif tag in _INLINE_MARK:
            self.out.append(_INLINE_MARK[tag])
        elif tag == "br":
            self.out.append("\n")
        elif tag == "img" and a.get("data-tex"):
            # Math rendered to an image (e.g. by MathJax), with its LaTeX kept alongside.
            self.out.append(_tex(a["data-tex"]))
        elif tag == "img" and a.get("alt"):
            self.out.append(f"[image: {a['alt']}]")
        elif tag == "blockquote":
            self.out.append("> ")
        elif tag in ("td", "th"):
            self.out.append("| ")

    def handle_endtag(self, tag: str) -> None:
        if self.math is not None:
            if tag == "math" and self.math[4] == 0:
                display, alt, tex, parts, _ = self.math
                self.math = None
                body = (tex if tex else None) or alt or "".join(parts)
                body = " ".join(body.split())
                self.out.append(f"\n\n$${body}$$\n\n" if display else f"${body}$")
            elif tag == "annotation" and self.math[2] == "":
                self.math[2] = "".join(self.math[3])
                self.math[3] = []
                self.math[4] -= 1
            else:
                self.math[4] -= 1
            return
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("ul", "ol") and self.lists:
            self.lists.pop()
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.out.append("\n```")
        elif tag == "code" and not self.pre:
            self.out.append("`")
        elif tag in _INLINE_MARK:
            self.out.append(_INLINE_MARK[tag])
        elif tag in ("td", "th"):
            self.out.append(" ")
        elif tag == "tr":
            self.out.append("|")
        if tag == "li":
            self.out.append("\n")
        elif tag in _BLOCK:
            self._block()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in ("br", "img", "hr"):
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        self._text(data)

    def result(self) -> str:
        text = "".join(self.out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"


def _tex(tex: str) -> str:
    r"""LaTeX from a data-tex attribute: \[...\] becomes display math, \(...\) inline."""
    tex = tex.strip()
    if m := re.fullmatch(r"\\\[(.*)\\\]", tex, re.DOTALL):
        return f"\n\n$${' '.join(m[1].split())}$$\n\n"
    if m := re.fullmatch(r"\\\((.*)\\\)", tex, re.DOTALL):
        tex = m[1]
    return f"${' '.join(tex.split())}$"


def html_to_markdown(source: str, doc: str = "") -> str:
    """Markdown for an HTML document. Element ids are kept as anchor markers (see _ANCHOR),
    named `<doc>#<id>`, plus one for the document itself (`<doc>#`)."""
    parser = _Converter(doc)
    parser.out.append(f"\x00{doc}#\x00")
    parser.feed(source)
    parser.close()
    return parser.result()


# -- EPUB ------------------------------------------------------------------------------

_NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
}


def read_epub(path: Path) -> tuple[str, list[Entry]]:
    """The book's text (spine order) and its outline: the EPUB 3 nav, else the EPUB 2 NCX,
    else the headings."""
    with zipfile.ZipFile(path) as z:
        container = ElementTree.fromstring(z.read("META-INF/container.xml"))
        opf_path = container.find(".//c:rootfile", _NS).get("full-path")
        opf = ElementTree.fromstring(z.read(opf_path))
        base = posixpath.dirname(opf_path)
        items = {
            i.get("id"): (
                posixpath.normpath(posixpath.join(base, unquote(i.get("href")))),
                i.get("media-type", ""),
                i.get("properties", ""),
            )
            for i in opf.find("opf:manifest", _NS)
        }
        spine = opf.find("opf:spine", _NS)
        docs = [
            items[ref.get("idref")][0]
            for ref in spine
            if ref.get("idref") in items and "nav" not in items[ref.get("idref")][2]
        ]
        parts = [
            html_to_markdown(z.read(d).decode("utf-8", errors="replace"), d)
            for d in docs
        ]
        marked = "\n".join(parts)

        toc = []
        nav = next((v for v in items.values() if "nav" in v[2]), None)
        if nav:
            toc = _nav_toc(z.read(nav[0]), posixpath.dirname(nav[0]))
        if not toc and (ncx_id := spine.get("toc")) in items:
            ncx = items[ncx_id][0]
            toc = _ncx_toc(z.read(ncx), posixpath.dirname(ncx))

    # Resolve anchor markers to offsets in the text without markers.
    positions: dict[str, int] = {}
    text_parts = []
    removed = 0
    last = 0
    for m in _ANCHOR.finditer(marked):
        text_parts.append(marked[last : m.start()])
        positions.setdefault(m[1], m.start() - removed)
        removed += len(m[0])
        last = m.end()
    text_parts.append(marked[last:])
    text = "".join(text_parts)

    entries = [
        Entry(level, title, positions[target])
        for level, title, target in toc
        if target in positions
    ]
    entries.sort(key=lambda e: e.offset)
    return text, entries or heading_entries(text)


def _target(href: str, base: str) -> str:
    file, _, fragment = unquote(href).partition("#")
    return f"{posixpath.normpath(posixpath.join(base, file))}#{fragment}"


def _nav_toc(data: bytes, base: str) -> list[tuple[int, str, str]]:
    root = ElementTree.fromstring(data)
    navs = root.iter(f"{{{_NS['xhtml']}}}nav")
    toc_nav = next(
        (n for n in navs if n.get(f"{{{_NS['epub']}}}type") == "toc"),
        None,
    )
    if toc_nav is None:
        return []
    out = []

    def walk(ol, level):
        for li in ol.findall("xhtml:li", _NS):
            a = li.find("xhtml:a", _NS)
            if a is not None and a.get("href"):
                title = " ".join("".join(a.itertext()).split())
                out.append((level, title, _target(a.get("href"), base)))
            for sub in li.findall("xhtml:ol", _NS):
                walk(sub, level + 1)

    for ol in toc_nav.findall("xhtml:ol", _NS):
        walk(ol, 1)
    return out


def _ncx_toc(data: bytes, base: str) -> list[tuple[int, str, str]]:
    root = ElementTree.fromstring(data)
    out = []

    def walk(point, level):
        label = point.find("ncx:navLabel/ncx:text", _NS)
        content = point.find("ncx:content", _NS)
        if label is not None and content is not None:
            out.append(
                (
                    level,
                    " ".join((label.text or "").split()),
                    _target(content.get("src"), base),
                )
            )
        for child in point.findall("ncx:navPoint", _NS):
            walk(child, level + 1)

    nav_map = root.find("ncx:navMap", _NS)
    for point in nav_map.findall("ncx:navPoint", _NS) if nav_map is not None else []:
        walk(point, 1)
    return out

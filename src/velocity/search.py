"""Keyword search over a source's text, so the tutor can find where a topic is covered
before reading a whole section.

Searching costs no model tokens: it runs locally and returns a few short snippets. For
PDFs it searches the text layer of every page (no OCR needed); where a page has no text
layer, its OCR'd text is used if cached. Text layers have two known flaws:
- math is garbled (`f(x)` can come out as `fsxd`), so formulas can't be searched for;
- some books lose the "f" of "fi", "fl" and "ff" ligatures ("definition" becomes
  "deinition"), so each query word is also tried in those forms (see `variants`).

Matching is case-insensitive and by substring, so "limit" also finds "limits". A section
matches when it contains every query word; if no section does, sections with most of
them are returned as partial matches.
"""

import re
import unicodedata
from dataclasses import dataclass

SNIPPET_CHARS = 90  # on each side of the match
MAX_PAGES_LISTED = 5
# Scoring (tuned on Stewart, 2026-11-09): see _page_score and _title_score.
PER_PAGE_CAP = 3
PHRASE_WEIGHT = 10
TITLE_WORD_WEIGHT = 20
TITLE_PHRASE_WEIGHT = 50
_F_BEFORE_IL = re.compile(r"f(?=[il])")
_F_BEFORE_F = re.compile(r"f(?=f)")
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


@dataclass(frozen=True)
class Chunk:
    """A searchable piece of a source: one PDF page, or one section of a text source."""

    section_id: str
    section_title: str
    page: int  # 1-indexed PDF page; 0 for text sources
    text: str


@dataclass(frozen=True)
class Hit:
    section_id: str
    section_title: str
    pages: list[int]  # the best match first (the snippet's page), then in page order
    snippet: str
    score: int
    complete: bool  # contains every query word


def normalize(text: str) -> str:
    """Lowercase, with ligature characters (ﬁ) spelled out, words broken across lines
    joined, and whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("­", "")
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    return " ".join(text.lower().split())


def query_terms(query: str) -> list[str]:
    """The query's words; very short ones are dropped unless nothing else is left."""
    words = _WORD.findall(query.lower())
    longer = [w for w in words if len(w) >= 3]
    return list(dict.fromkeys(longer or words))


def variants(term: str) -> set[str]:
    """The term as typed, and as text layers that lose a ligature's "f" store it. Seen in
    Stewart (2026-11-09): "definition" as "deinition", "flux" as "lux", "sufficient" as
    "suficient", "coefficient" also as "coeicient", "differentiation" also as
    "diferentiation"."""
    before_f = _F_BEFORE_F.sub("", term)
    forms = {term, _F_BEFORE_IL.sub("", term), before_f, _F_BEFORE_IL.sub("", before_f)}
    return {f for f in forms if len(f.replace(" ", "")) >= 3} or {term}


def _find(text: str, term: str) -> int:
    """Offset of the first occurrence of any variant of the term, or -1."""
    found = [i for v in variants(term) if (i := text.find(v)) >= 0]
    return min(found, default=-1)


def section_at(outline: list, page: int):
    """The outline entry a PDF page belongs to: the last one starting on or before it
    (the outline is in reading order, so this is the most specific). None before the
    first entry (front matter)."""
    return next((s for s in reversed(outline) if s.start_page <= page), None)


def _count(text: str, term: str) -> int:
    return max(text.count(v) for v in variants(term))


def _page_score(text: str, terms: list[str], phrase: str) -> int:
    """Occurrences, capped per page so that pages listing a word many times (an index)
    don't outrank pages that explain it; the whole phrase counts most."""
    words = sum(min(_count(text, t), PER_PAGE_CAP) for t in terms)
    return words + PHRASE_WEIGHT * min(_count(text, phrase), PER_PAGE_CAP)


def _title_score(title: str, terms: list[str], phrase: str) -> int:
    """A section named after the query is most likely the one that teaches it."""
    title = normalize(title)
    score = TITLE_WORD_WEIGHT * sum(1 for t in terms if _find(title, t) >= 0)
    return score + (TITLE_PHRASE_WEIGHT if _find(title, phrase) >= 0 else 0)


def search(chunks: list[Chunk], query: str, max_results: int = 6) -> list[Hit]:
    terms = query_terms(query)
    if not terms:
        return []
    phrase = " ".join(terms)
    by_section: dict[str, list[tuple[Chunk, str, int, int]]] = {}
    for chunk in chunks:
        text = normalize(chunk.text)
        matched = sum(1 for t in terms if _find(text, t) >= 0)
        if not matched:
            continue
        score = _page_score(text, terms, phrase)
        by_section.setdefault(chunk.section_id, []).append(
            (chunk, text, matched, score)
        )

    hits = []
    for found in by_section.values():
        # A section counts as complete if its pages together contain every word.
        best_matched = max(m for _, _, m, _ in found)
        joined = " ".join(t for _, t, _, _ in found)
        complete = all(_find(joined, t) >= 0 for t in terms)
        chunk, text, _, _ = max(found, key=lambda f: (f[2], f[3]))
        hits.append(
            Hit(
                chunk.section_id,
                chunk.section_title,
                # The best page (the snippet's) first, then the rest in order.
                [chunk.page] * bool(chunk.page)
                + sorted(c.page for c, _, _, _ in found if c.page != chunk.page),
                _snippet(text, terms, phrase),
                sum(s for _, _, _, s in found)
                + 100 * best_matched
                + _title_score(chunk.section_title, terms, phrase),
                complete,
            )
        )
    complete = [h for h in hits if h.complete]
    chosen = complete or hits
    return sorted(chosen, key=lambda h: (-h.score, h.pages[:1]))[:max_results]


def _snippet(text: str, terms: list[str], phrase: str) -> str:
    at = _find(text, phrase)
    if at < 0:
        at = min((i for t in terms if (i := _find(text, t)) >= 0), default=0)
    start, end = max(0, at - SNIPPET_CHARS), at + SNIPPET_CHARS + len(phrase)
    return (
        ("…" if start else "")
        + text[start:end].strip()
        + ("…" if end < len(text) else "")
    )


def format_hits(source: str, query: str, hits: list[Hit], note: str = "") -> str:
    """The tool result: one line per section, with the pages that matched."""
    if not hits:
        return (
            f"No matches for {query!r} in {source}.{note} Try other words (a synonym, or "
            "a shorter form), or use get_outline."
        )
    partial = not hits[0].complete
    head = f"{'Partial matches' if partial else 'Matches'} for {query!r} in {source}"
    lines = [
        head
        + " (snippets come from the text layer: math in them may be garbled; read the "
        "section for the real text):" + note
    ]
    for h in hits:
        pages = ""
        if h.pages:
            best, others = h.pages[0], h.pages[1:]
            pages = f" (best match PDF p. {best}"
            if others:
                shown = ", ".join(map(str, others[:MAX_PAGES_LISTED]))
                more = "…" if len(others) > MAX_PAGES_LISTED else ""
                pages += f"; also on pp. {shown}{more}"
            pages += ")"
        lines.append(
            f"- {source} §{h.section_id}: {h.section_title}{pages}: {h.snippet}"
        )
    return "\n".join(lines)

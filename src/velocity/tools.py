"""The tutor's tools for one subject, as plain functions (wrapped for MCP in mcp_server.py).

Return values are plain text meant for the model to read.
"""

import os
import re
from collections.abc import Callable
from datetime import date
from typing import Literal

from velocity.config import Config, Subject
from velocity.notes import NotesError
from velocity.notes import list_notes as _list_notes
from velocity.notes import read_note as _read_note
from velocity.ocr import PageOcr
from velocity.pdf_source import PdfSource
from velocity.session import RECAP_TITLE, Session, active_session_id, record_dir
from velocity.state import (
    LearnerState,
    Outcome,
    StateError,
    Status,
    load_state,
    save_state,
)
from velocity.text_source import TEXT_SUFFIXES, TextSource

BlockKind = Literal["example", "summary", "note", "tip", "question", "warning", "quote"]


class ToolError(Exception):
    """An error the model should see and can act on."""


def default_ocr(config: Config) -> PageOcr:
    if config.llama_cpp_binary and "LLAMA_CPP_BINARY" not in os.environ:
        # Read by marker's OCR engine (surya) when it is imported, so set it first.
        os.environ["LLAMA_CPP_BINARY"] = config.llama_cpp_binary.as_posix()
    try:
        import marker  # noqa: F401

        from velocity.ocr import MarkerOcr
    except ImportError:
        raise ToolError(
            "Reading PDF sources needs the optional OCR dependencies "
            "(install with `uv sync --group ocr`)."
        ) from None
    return MarkerOcr()


class TutorTools:
    def __init__(
        self,
        config: Config,
        subject: Subject,
        ocr_factory: Callable[[], PageOcr] | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._today = today
        self.config = config
        self.subject = subject
        self._ocr_factory = ocr_factory or (lambda: default_ocr(config))
        self._ocr: PageOcr | None = None
        self._sources: dict[str, PdfSource] = {}

    # -- sources -------------------------------------------------------------------

    def list_sources(self) -> str:
        """List the sources attached to this subject."""
        if not self.subject.sources:
            return "This subject has no sources. Teach from general knowledge and say so once."
        return "\n".join(f"- {s.name} ({s.path.name})" for s in self.subject.sources)

    def _source(self, name: str) -> PdfSource:
        if name in self._sources:
            return self._sources[name]
        ref = next((s for s in self.subject.sources if s.name == name), None)
        if ref is None:
            names = ", ".join(s.name for s in self.subject.sources) or "none"
            raise ToolError(f"Unknown source {name!r}. Available: {names}.")
        if not ref.path.is_file():
            raise ToolError(f"Source file for {name!r} not found at {ref.path}.")
        if ref.path.suffix.lower() in TEXT_SUFFIXES:
            self._sources[name] = TextSource(ref.path)
            return self._sources[name]
        if ref.path.suffix.lower() != ".pdf":
            raise ToolError(f"Source {name!r} has an unsupported file type.")

        def ocr(*args):
            if self._ocr is None:
                self._ocr = self._ocr_factory()
            return self._ocr(*args)

        self._sources[name] = PdfSource(ref.path, ocr)
        return self._sources[name]

    def get_outline(self, source: str) -> str:
        """Table of contents of a source: section ids, titles, and (for PDFs) page ranges."""
        outline = self._source(source).outline()
        if not outline:
            return (
                f"{source} has no table of contents or headings, so its sections can't be "
                "read separately."
            )
        lines = [f"Outline of {source} (cite sections as '{source} §<id>'):"]
        for s in outline:
            indent = "  " * (s.level - 1)
            pages = f", PDF pp. {s.start_page}-{s.end_page}" if s.start_page else ""
            lines.append(f"{indent}{s.title} [id: {s.id}{pages}]")
        return "\n".join(lines)

    def read_section(self, source: str, section_id: str) -> str:
        """Full text of one section as markdown with LaTeX math. First reads can take a minute."""
        try:
            text = self._source(source).read_section(section_id)
        except KeyError:
            raise ToolError(
                f"No section {section_id!r} in {source}. Use get_outline to find section ids."
            ) from None
        header = f"{source} §{text.id}: {text.title}"
        if text.first_page:
            header += f" (PDF pp. {text.first_page}-{text.last_page})"
        warnings = "".join(f"\nWARNING: {w}" for w in text.warnings)
        return f"{header}{warnings}\n\n{text.markdown}"

    def get_exercises(self, source: str, section_id: str) -> str:
        """The exercise set at the end of a section (everything after its EXERCISES heading)."""
        text = self.read_section(source, section_id)
        # A heading ("## 2.4 EXERCISES") or a label paragraph ("*Exercises I.*").
        heading = re.search(
            r"^(?:#{1,6}[^\n]*\bexercises\b|[*_]{1,2}exercises\b)[^\n]*$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        if heading is None:
            raise ToolError(
                f"No exercises heading found in {source} §{section_id}. Use read_section and "
                "look for problems in the text, or write your own."
            )
        header = text.split("\n", 1)[0]
        return f"Exercises from {header}\n\n{text[heading.start() :].strip()}"

    # -- session log ---------------------------------------------------------------

    def _session(self, session_id: str) -> Session:
        session = Session(
            self.subject.slug,
            session_id,
            record_dir(self.subject.slug),
            self.subject.sessions_dir,
        )
        if not session.record_path.exists():
            raise ToolError(f"No session {session_id!r} for {self.subject.name}.")
        return session

    def _active(self) -> Session:
        session_id = active_session_id(self.subject.slug)
        if session_id is None:
            raise ToolError("No active session; the session log is not open.")
        return self._session(session_id)

    def append_log(self, title: str, text: str, kind: BlockKind = "example") -> str:
        """Add a block (worked problem, recap, definition...) to the current session log.

        It appears as an Obsidian callout, in order with the conversation. Use it for
        material worth keeping; the conversation itself is logged automatically.
        """
        session = self._active()
        session.append("block", text, title=title, kind=kind)
        return f"Added '{title}' to the session log."

    def list_sessions(self) -> str:
        """Past and current sessions for this subject, newest first."""
        ids = sorted(
            (p.stem for p in record_dir(self.subject.slug).glob("*.jsonl")),
            key=lambda i: (i.rsplit("-", 1)[0], int(i.rsplit("-", 1)[1])),
            reverse=True,
        )
        if not ids:
            return "No sessions yet."
        active = active_session_id(self.subject.slug)
        return "\n".join(f"- {i}{' (current)' if i == active else ''}" for i in ids)

    def read_session(self, session_id: str) -> str:
        """The full dialogue of a session, from the tamper-proof record."""
        from velocity.session import render_event

        events = sorted(self._session(session_id).events(), key=lambda e: e.ts)
        return f"Session {session_id}:\n\n" + "".join(
            render_event(e, annotations=True) for e in events
        )

    def check_log(self) -> str:
        """Compare the current session's markdown log with the record.

        Reports learner annotations (text they added) and text that went missing.
        """
        check = self._active().check_log()
        parts = []
        if check.annotations:
            parts.append(
                "Learner annotations:\n"
                + "\n".join(f"- {a}" for a in check.annotations)
            )
        if check.missing:
            parts.append(
                "Missing from the log (deleted or edited):\n"
                + "\n".join(f"- {m}" for m in check.missing)
                + "\nAsk the learner before calling restore_log."
            )
        return "\n\n".join(parts) or "The log matches the record; no annotations."

    def restore_log(self) -> str:
        """Rebuild the current session's markdown from the record.

        This drops the learner's annotations in that file, so only call it after the
        learner has agreed.
        """
        self._active().restore_log()
        return "Restored the session log from the record."

    def end_session(self, recap: str) -> str:
        """Wrap up the session (used by /end-session).

        recap: markdown summary for the learner's log: what was covered, how it went, what to
        review next. It is added to the log, shown to you at the start of the next session,
        and the log is closed after your final message this turn.
        """
        session = self._active()
        if session.wrap_up_requested:
            raise ToolError("This session is already being wrapped up.")
        session.append("block", recap, title=RECAP_TITLE, kind="summary")
        session.append("event", "requested", kind="wrap-up")
        return (
            "Recap added. The log closes after your final message this turn; anything after "
            "that is not logged, so tell the learner they can exit (/exit or Ctrl+D)."
        )

    # -- learner state -------------------------------------------------------------

    def _update_state(self, change: Callable[[LearnerState, date], str]) -> str:
        try:
            state = load_state(self.subject.dir)
            message = change(state, self._today())
        except StateError as e:
            raise ToolError(str(e)) from None
        save_state(state, self.subject.dir, self.subject.name, self._today())
        return message

    def record_question(
        self,
        topic: str,
        question: str,
        outcome: Outcome,
        note: str = "",
        source: str | None = None,
    ) -> str:
        """Record the outcome of a question the learner answered.

        Call this after every question you asked and the learner answered. outcome is
        "correct", "hinted" (needed hints), or "incorrect". topic is a short name, e.g.
        "epsilon-delta definition"; reuse names consistently. note: what went wrong or
        right, in a few words. source: section it came from, e.g. "Stewart §2.4".
        """
        session = active_session_id(self.subject.slug) or ""

        def change(state: LearnerState, today: date) -> str:
            t = state.record_question(
                topic, question, outcome, session, today, note, source
            )
            return f"Recorded ({outcome}). {t.name}: {t.status}, next review {t.next_review}."

        return self._update_state(change)

    def set_topic_status(
        self, topic: str, status: Status, source: str | None = None
    ) -> str:
        """Set a topic's status: "introduced", "practicing", or "solid".

        Use "introduced" when you first teach a topic, and "solid" once the learner has
        shown mastery across several questions.
        """

        def change(state: LearnerState, today: date) -> str:
            t = state.set_status(topic, status, today, source)
            return f"{t.name} is now {t.status}."

        return self._update_state(change)

    def open_thread(self, text: str) -> str:
        """Note something to come back to (an unresolved confusion, a deferred question)."""
        session = active_session_id(self.subject.slug) or ""

        def change(state: LearnerState, today: date) -> str:
            return f"Opened thread #{state.open_thread(text, session, today).id}."

        return self._update_state(change)

    def close_thread(self, thread_id: int) -> str:
        """Close an open thread once it is resolved."""

        def change(state: LearnerState, today: date) -> str:
            return f"Closed thread #{state.close_thread(thread_id, today).id}."

        return self._update_state(change)

    def read_state(self) -> str:
        """What the learner has studied: due reviews, weak spots, open threads, topics."""
        try:
            state = load_state(self.subject.dir)
        except StateError as e:
            raise ToolError(str(e)) from None
        summary = state.summary(self._today(), limit=50)
        topics = "\n".join(
            f"- {t.name}: {t.status}, last seen {t.last_seen}, next review {t.next_review or 'none'}"
            + (f", source {t.source}" if t.source else "")
            for t in state.topics.values()
        )
        return summary + ("\n\nAll topics:\n" + topics if topics else "")

    # -- learner notes (read-only) ---------------------------------------------------

    def list_notes(self) -> str:
        """The learner's notes for this subject that you may read: path, title, last modified.

        Notes are the learner's own; you can read them but never change them.
        """
        if not self.subject.note_folders:
            return (
                "No note folders are configured for this subject. The learner can add one with: "
                f"velocity notes add {self.subject.slug} <folder in their vault>"
            )
        notes = _list_notes(self.config, self.subject)
        if not notes:
            return "No (non-private) notes in this subject's note folders yet."
        return "\n".join(
            f"- {n.path}: {n.title} (modified {n.modified})" for n in notes[:200]
        )

    def read_note(self, path: str) -> str:
        """Read one of the learner's notes (path as shown by list_notes). Read-only."""
        try:
            return f"Note {path}:\n\n" + _read_note(self.config, self.subject, path)
        except NotesError as e:
            raise ToolError(str(e)) from None

"""The tutor's tools for one subject, as plain functions (wrapped for MCP in mcp_server.py).

Return values are plain text meant for the model to read.
"""

from collections.abc import Callable
from typing import Literal

from velocity.config import Config, Subject
from velocity.ocr import PageOcr
from velocity.pdf_source import PdfSource
from velocity.session import Session, active_session_id, record_dir

BlockKind = Literal["example", "summary", "note", "tip", "question", "warning", "quote"]


class ToolError(Exception):
    """An error the model should see and can act on."""


def _default_ocr() -> PageOcr:
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
        ocr_factory: Callable[[], PageOcr] = _default_ocr,
    ) -> None:
        self.config = config
        self.subject = subject
        self._ocr_factory = ocr_factory
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
        if ref.path.suffix.lower() != ".pdf":
            raise ToolError(
                f"Source {name!r} is not a PDF; only PDF sources are supported so far."
            )
        if not ref.path.is_file():
            raise ToolError(f"Source file for {name!r} not found at {ref.path}.")

        def ocr(*args):
            if self._ocr is None:
                self._ocr = self._ocr_factory()
            return self._ocr(*args)

        self._sources[name] = PdfSource(ref.path, ocr)
        return self._sources[name]

    def get_outline(self, source: str) -> str:
        """Table of contents of a source: section ids, titles, and PDF page ranges."""
        lines = [f"Outline of {source} (cite sections as '{source} §<id>'):"]
        for s in self._source(source).outline():
            indent = "  " * (s.level - 1)
            lines.append(
                f"{indent}{s.title} [id: {s.id}, PDF pp. {s.start_page}-{s.end_page}]"
            )
        return "\n".join(lines)

    def read_section(self, source: str, section_id: str) -> str:
        """Full text of one section as markdown with LaTeX math. First reads can take a minute."""
        try:
            text = self._source(source).read_section(section_id)
        except KeyError:
            raise ToolError(
                f"No section {section_id!r} in {source}. Use get_outline to find section ids."
            ) from None
        header = f"{source} §{text.id}: {text.title} (PDF pp. {text.first_page}-{text.last_page})"
        warnings = "".join(f"\nWARNING: {w}" for w in text.warnings)
        return f"{header}{warnings}\n\n{text.markdown}"

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
        return f"Session {session_id}:\n\n" + "".join(render_event(e) for e in events)

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

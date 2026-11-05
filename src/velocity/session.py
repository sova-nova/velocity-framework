"""Tutoring sessions: an append-only JSONL record (source of truth) and a markdown log view.

The record lives outside the vault; the markdown log lives in <vault>/Tutor/<subject>/sessions/
and is rendered in Obsidian. Events are appended to the record as they happen and rendered to
the markdown in batches (once per turn), sorted by timestamp, so blocks the tutor adds mid-turn
land between the right pieces of text. The tool only ever appends to the markdown; text the
learner adds there is treated as annotation, and text missing from it can be detected.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from velocity.config import data_dir

Role = Literal["learner", "tutor", "block", "event"]

ENDED_WITH_WRAP_UP = "end-session"
ENDED_WITHOUT_WRAP_UP = "no-wrap-up"
RECAP_TITLE = "Session recap"


@dataclass(frozen=True)
class Event:
    ts: str  # ISO 8601, sortable
    role: Role
    text: str
    title: str | None = None  # blocks: callout title
    kind: str | None = (
        None  # blocks: callout type (example, summary, note, ...); events: name
    )
    meta: dict | None = (
        None  # events: extra data (the start event holds the source names)
    )


def render_event(event: Event, annotations: bool = False) -> str:
    """Markdown for one event. Session-start events render to the header via render_header.

    Annotations are learner text already in the markdown, so they render only when
    annotations=True (for reading a session back from the record).
    """
    if event.role == "learner":
        return f"**You:** {event.text.strip()}\n\n"
    if event.role == "tutor":
        return f"**Tutor:** {event.text.strip()}\n\n"
    if event.role == "block":
        lines = [f"> [!{event.kind or 'note'}] {event.title or ''}".rstrip()]
        lines += [f"> {line}".rstrip() for line in event.text.strip().splitlines()]
        return "\n".join(lines) + "\n\n"
    if event.kind == "annotation" and annotations:
        return f"_(learner annotation)_ {event.text.strip()}\n\n"
    if event.kind == "resume":
        return "\n*Session resumed.*\n\n"
    if event.kind == "end":
        how = (
            "Session ended with /end-session."
            if event.text == ENDED_WITH_WRAP_UP
            else "Session ended without wrap-up."
        )
        return f"---\n*{how}*\n"
    return ""


def render_header(
    subject_slug: str,
    subject_name: str,
    session_id: str,
    started: str,
    sources: list[str],
) -> str:
    date, n = session_id.rsplit("-", 1)
    props = [
        "---",
        f"subject: {subject_slug}",
        f"session: {session_id}",
        f"started: {started[:16]}",
        f"sources: [{', '.join(sources)}]",
        "---",
    ]
    return "\n".join(props) + f"\n# {subject_name} — {date} ({n})\n\n"


@dataclass(frozen=True)
class LogCheck:
    missing: list[str]  # rendered text no longer in the markdown (deleted or edited)
    annotations: list[str]  # text in the markdown the tool did not write


class Session:
    def __init__(
        self, subject_slug: str, session_id: str, record_dir: Path, log_dir: Path
    ):
        self.subject_slug = subject_slug
        self.id = session_id
        self.record_path = record_dir / f"{session_id}.jsonl"
        self.log_path = log_dir / f"{session_id}.md"
        self._rendered_path = record_dir / f"{session_id}.rendered"

    # -- lifecycle ---------------------------------------------------------------

    @classmethod
    def start(
        cls,
        subject_slug: str,
        subject_name: str,
        record_dir: Path,
        log_dir: Path,
        sources: list[str],
        now: datetime | None = None,
    ) -> "Session":
        now = now or datetime.now().astimezone()
        date = now.date().isoformat()
        record_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        n = 1 + sum(1 for _ in record_dir.glob(f"{date}-*.jsonl"))
        while (record_dir / f"{date}-{n}.jsonl").exists() or (
            log_dir / f"{date}-{n}.md"
        ).exists():
            n += 1
        session = cls(subject_slug, f"{date}-{n}", record_dir, log_dir)
        started = Event(
            now.isoformat(),
            "event",
            subject_name,
            kind="start",
            meta={"sources": sources},
        )
        session._write_record(started)
        session.log_path.write_text(
            render_header(subject_slug, subject_name, session.id, started.ts, sources),
            encoding="utf-8",
        )
        session._set_rendered(1)
        return session

    def append(
        self,
        role: Role,
        text: str,
        *,
        title: str | None = None,
        kind: str | None = None,
        now: datetime | None = None,
    ) -> Event:
        """Record an event. It reaches the markdown on the next render()."""
        if self.ended:
            raise RuntimeError(f"session {self.id} has ended")
        event = Event(
            (now or datetime.now().astimezone()).isoformat(), role, text, title, kind
        )
        self._write_record(event)
        return event

    def render(self) -> None:
        """Append all not-yet-rendered events to the markdown, in timestamp order."""
        events = self.events()
        done = self._rendered()
        pending = sorted(events[done:], key=lambda e: e.ts)
        if pending:
            with self.log_path.open("a", encoding="utf-8") as f:
                # Text the learner typed at the end (saved without a trailing blank line)
                # must not run into the next turn.
                f.write(_separator(self.log_path) + "".join(map(render_event, pending)))
        self._set_rendered(len(events))

    def end(
        self, how: str = ENDED_WITHOUT_WRAP_UP, now: datetime | None = None
    ) -> None:
        if self.ended:
            return
        self.append("event", how, kind="end", now=now)
        self.render()

    def _last_end(self) -> Event | None:
        """The end event, if the session is currently ended (not reopened since)."""
        last = None
        for e in self.events():
            if e.kind == "end":
                last = e
            elif e.kind == "resume":
                last = None
        return last

    @property
    def ended(self) -> bool:
        return self._last_end() is not None

    @property
    def wrapped_up(self) -> bool:
        """Ended with /end-session. A wrapped-up session is final and is never reopened."""
        last = self._last_end()
        return last is not None and last.text == ENDED_WITH_WRAP_UP

    def reopen(self, now: datetime | None = None) -> None:
        """Continue a session that ended without wrap-up (the learner resumed it)."""
        if not self.ended or self.wrapped_up:
            raise RuntimeError(f"session {self.id} cannot be reopened")
        event = Event(
            (now or datetime.now().astimezone()).isoformat(), "event", "", kind="resume"
        )
        self._write_record(event)
        self.render()

    @property
    def wrap_up_requested(self) -> bool:
        return any(e.kind == "wrap-up" for e in self.events()) and not self.wrapped_up

    def recap(self) -> str | None:
        """The recap written by /end-session, if the session was wrapped up."""
        recaps = [
            e.text
            for e in self.events()
            if e.role == "block" and e.kind == "summary" and e.title == RECAP_TITLE
        ]
        return recaps[-1] if recaps else None

    # -- reading -------------------------------------------------------------------

    def events(self) -> list[Event]:
        """All recorded events in recording order."""
        if not self.record_path.exists():
            return []
        with self.record_path.open(encoding="utf-8") as f:
            return [Event(**json.loads(line)) for line in f if line.strip()]

    def check_log(self) -> LogCheck:
        """Compare the markdown log with what the record says was rendered into it."""
        events = self.events()[: self._rendered()]
        start = next((e for e in events if e.kind == "start"), None)
        rendered = [
            render_event(e)
            for e in sorted((e for e in events if e is not start), key=lambda e: e.ts)
        ]
        text = (
            self.log_path.read_text(encoding="utf-8") if self.log_path.exists() else ""
        )

        # Skip the header: Obsidian may legitimately rewrite the properties block.
        cursor = text.find("\n# ")
        cursor = text.find("\n", cursor + 1) + 1 if cursor >= 0 else 0
        missing, annotations = [], []
        for chunk in (c for c in rendered if c.strip()):
            pos = text.find(chunk.strip(), cursor)
            if pos < 0:
                missing.append(chunk.strip())
                continue
            if extra := text[cursor:pos].strip():
                annotations.append(extra)
            cursor = pos + len(chunk.strip())
        if extra := text[cursor:].strip():
            annotations.append(extra)
        return LogCheck(missing, annotations)

    def restore_log(self) -> None:
        """Rewrite the markdown from the record. Learner annotations are lost; callers ask first."""
        events = self.events()[: self._rendered()]
        start = next(e for e in events if e.kind == "start")
        sources = (start.meta or {}).get("sources", [])
        header = render_header(
            self.subject_slug, start.text, self.id, start.ts, sources
        )
        body = "".join(
            render_event(e)
            for e in sorted((e for e in events if e is not start), key=lambda e: e.ts)
        )
        self.log_path.write_text(header + body, encoding="utf-8")

    # -- internals -----------------------------------------------------------------

    def _write_record(self, event: Event) -> None:
        with self.record_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def _rendered(self) -> int:
        try:
            return int(self._rendered_path.read_text())
        except FileNotFoundError:
            return 0

    def _set_rendered(self, n: int) -> None:
        self._rendered_path.write_text(str(n))


def _separator(path: Path) -> str:
    """The newlines needed so text appended to `path` starts a new paragraph."""
    with path.open("rb") as f:
        size = f.seek(0, 2)
        f.seek(max(0, size - 2))
        tail = f.read()
    if not tail or tail.endswith(b"\n\n"):
        return ""
    return "\n" if tail.endswith(b"\n") else "\n\n"


def record_dir(subject_slug: str) -> Path:
    return data_dir() / "records" / subject_slug


def set_active(subject_slug: str, session_id: str | None) -> None:
    """Mark the session the tutor's tools should write to (None clears it)."""
    pointer = record_dir(subject_slug) / "ACTIVE"
    if session_id is None:
        pointer.unlink(missing_ok=True)
        return
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(session_id)


def active_session_id(subject_slug: str) -> str | None:
    try:
        return (record_dir(subject_slug) / "ACTIVE").read_text().strip() or None
    except FileNotFoundError:
        return None

"""Tutoring sessions: an append-only JSONL record (source of truth) and a markdown log view.

The record lives outside the vault; the markdown log lives in <vault>/Tutor/<subject>/sessions/
and is rendered in Obsidian. Events are appended to the record as they happen and rendered to
the markdown in batches (once per turn), sorted by timestamp, so blocks the tutor adds mid-turn
land between the right pieces of text. The tool only ever appends to the markdown; text the
learner adds there is treated as annotation, and text missing from it can be detected.
"""

import contextlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from velocity.config import data_dir

Role = Literal["learner", "tutor", "block", "event"]

ENDED_WITH_WRAP_UP = "end-session"
ENDED_WITHOUT_WRAP_UP = "no-wrap-up"
RECAP_TITLE = "Session recap"

# A recorded answer, as a line in the log (decided 2026-11-10): the web page draws it as a
# divider colored by the outcome.
RECORDED_MARKS = {"correct": "✓", "hinted": "~", "partial": "◐", "incorrect": "✗"}


def recorded_line(outcome: str, topic: str) -> str:
    return f"*{RECORDED_MARKS.get(outcome, '·')} Recorded: {outcome} · {topic}*"


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
        text = event.text.strip()
        # A reply that opens with a callout (decided 2026-11-10) needs it on its own line.
        separator = "\n\n" if text.startswith(">") else " "
        return f"**Tutor:**{separator}{text}\n\n"
    if event.role == "block":
        lines = [f"> [!{event.kind or 'note'}] {event.title or ''}".rstrip()]
        lines += [f"> {line}".rstrip() for line in event.text.strip().splitlines()]
        return "\n".join(lines) + "\n\n"
    if event.kind == "annotation" and annotations:
        return f"_(learner annotation)_ {event.text.strip()}\n\n"
    if event.kind == "resume":
        return "\n*Session resumed.*\n\n"
    if event.kind == "recorded":
        meta = event.meta or {}
        return recorded_line(event.text, meta.get("topic", "")) + "\n\n"
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
    continues: str | None = None,
    title: str | None = None,
) -> str:
    date, n = session_id.rsplit("-", 1)
    props = [
        "---",
        f"subject: {subject_slug}",
        f"session: {session_id}",
        f"started: {started[:16]}",
        f"sources: [{', '.join(sources)}]",
    ]
    if continues:
        props.append(f'continues: "[[{continues}]]"')  # an Obsidian link to that log
    if title:
        props.append(title_property(title))
    props.append("---")
    return "\n".join(props) + f"\n# {subject_name} — {date} ({n})\n\n"


MAX_TITLE = 100


def title_property(title: str) -> str:
    """The log's `title:` property line (a quoted YAML string)."""
    return f"title: {json.dumps(title, ensure_ascii=False)}"


def with_title_property(text: str, title: str) -> str:
    """The log text with its properties block's `title:` line set. Unchanged if the log
    has no properties block (e.g. the learner removed it)."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    lines = text[4:end].split("\n")
    lines = [line for line in lines if not line.startswith("title:")]
    lines.append(title_property(title))
    return "---\n" + "\n".join(lines) + text[end:]


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
        continues: str | None = None,
    ) -> "Session":
        """A new session. `continues` names a finished session this one picks up from."""
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
            meta={
                "sources": sources,
                **({"continues": continues} if continues else {}),
            },
        )
        session._write_record(started)
        session.log_path.write_text(
            render_header(
                subject_slug,
                subject_name,
                session.id,
                started.ts,
                sources,
                continues,
            ),
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
        meta: dict | None = None,
        now: datetime | None = None,
    ) -> Event:
        """Record an event. It reaches the markdown on the next render()."""
        if self.ended:
            raise RuntimeError(f"session {self.id} has ended")
        event = Event(
            (now or datetime.now().astimezone()).isoformat(),
            role,
            text,
            title,
            kind,
            meta,
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

    @property
    def empty(self) -> bool:
        """Nothing happened in it: no message from either side, no title, no log block,
        no tool call, no annotation; only its start, end, resume and engine events."""
        events = self.events()
        return bool(events) and all(
            e.role == "event" and e.kind in ("start", "end", "resume", "engine")
            for e in events
        )

    def end_or_remove(self) -> bool:
        """End the session, or remove it if it is empty and the learner hasn't written in
        its log (decided 2026-11-10). Returns True if it was removed."""
        if self.empty and self._log_untouched():
            self.remove()
            return True
        self.end()
        return False

    def remove(self) -> None:
        """Delete the session's record, its markdown log, and Claude Code's mappings to it.
        Only for sessions with nothing in them (see end_or_remove)."""
        mappings = self.record_path.parent / "claude-sessions"
        if mappings.is_dir():
            for mapping in mappings.iterdir():
                with contextlib.suppress(OSError):
                    if mapping.read_text().strip() == self.id:
                        mapping.unlink()
        for path in (self.log_path, self._rendered_path, self.record_path):
            path.unlink(missing_ok=True)

    def _log_untouched(self) -> bool:
        if not self.log_path.exists():
            return True
        check = self.check_log()
        return not check.annotations and not check.missing

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
    def title(self) -> str | None:
        """The latest title the tutor gave this session, if any."""
        titles = [e.text for e in self.events() if e.kind == "title"]
        return titles[-1] if titles else None

    @property
    def continues(self) -> str | None:
        """The finished session this one picks up from, if any."""
        start = next((e for e in self.events() if e.kind == "start"), None)
        return (start.meta or {}).get("continues") if start else None

    def set_title(self, title: str, now: datetime | None = None) -> str:
        """Record the session's title and show it as the log's `title:` property.

        The only change velocity makes to existing log text: one line of the properties
        block, which is metadata (Obsidian edits it too). The dialogue is never rewritten.
        """
        title = " ".join(title.split())
        if not title:
            raise ValueError("the title is empty")
        if len(title) > MAX_TITLE:
            raise ValueError(f"the title is longer than {MAX_TITLE} characters")
        self.append("event", title, kind="title", now=now)
        if self.log_path.exists():
            text = self.log_path.read_text(encoding="utf-8")
            updated = with_title_property(text, title)
            if updated != text:
                self.log_path.write_text(updated, encoding="utf-8")
        return title

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

    def rendered_text(self) -> str:
        """The markdown log as the record says it was rendered (without learner edits)."""
        events = self.events()[: self._rendered()]
        start = next(e for e in events if e.kind == "start")
        meta = start.meta or {}
        header = render_header(
            self.subject_slug,
            start.text,
            self.id,
            start.ts,
            meta.get("sources", []),
            meta.get("continues"),
            self.title,
        )
        body = "".join(
            render_event(e)
            for e in sorted((e for e in events if e is not start), key=lambda e: e.ts)
        )
        return header + body

    def restore_log(self) -> None:
        """Rewrite the markdown from the record. Learner annotations are lost; callers ask first."""
        self.log_path.write_text(self.rendered_text(), encoding="utf-8")

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


SESSION_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-\d+$")


def session_order(session_id: str) -> tuple[str, int]:
    day, n = session_id.rsplit("-", 1)
    return day, int(n)


@dataclass(frozen=True)
class SessionSummary:
    id: str
    title: str | None
    started: str | None
    turns: int  # learner messages
    first_message: str
    ended: str | None  # "wrapped-up", "no-wrap-up", or None while open
    has_recap: bool
    continues: str | None


def session_summaries(subject_slug: str, log_dir: Path) -> list[SessionSummary]:
    """A subject's sessions, newest first, with a short summary of each."""
    records = record_dir(subject_slug)
    ids = [p.stem for p in records.glob("*.jsonl") if SESSION_ID.match(p.stem)]
    active = active_session_id(subject_slug)
    out = []
    for session_id in sorted(ids, key=session_order, reverse=True):
        session = Session(subject_slug, session_id, records, log_dir)
        events = session.events()
        start = next((e for e in events if e.kind == "start"), None)
        learner = [e.text for e in events if e.role == "learner"]
        if session.wrapped_up:
            ended = "wrapped-up"
        elif session.ended or session_id != active:
            # A record without an end event that isn't the active session was cut off
            # (its process stopped, or another session took over): it is paused.
            ended = "no-wrap-up"
        else:
            ended = None  # open now
        out.append(
            SessionSummary(
                id=session_id,
                title=session.title,
                started=start.ts if start else None,
                turns=len(learner),
                first_message=learner[0][:120] if learner else "",
                ended=ended,
                has_recap=session.recap() is not None,
                continues=session.continues,
            )
        )
    return out


# -- searching sessions (decided 2026-11-10) ---------------------------------------------


@dataclass(frozen=True)
class SessionMatch:
    """A session a search found: by its name (title, or first message), or by its text,
    with a snippet around the first match."""

    session: SessionSummary
    in_title: bool
    snippet: str | None


SNIPPET_BEFORE = 60
SNIPPET_AFTER = 100
_MATH = re.compile(r"\$\$[\s\S]+?\$\$|\$[^\s$](?:[^$\n]*?[^\s$])?\$")
_LOG_PROPERTIES = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def search_words(query: str) -> list[str]:
    return query.lower().split()


def search_sessions(subject_slug: str, log_dir: Path, query: str) -> list[SessionMatch]:
    """Sessions with every word of `query` (any case, any order) in their name or their
    log (the record when the log is missing). Name matches come first, then text
    matches; newest first within each."""
    words = search_words(query)
    if not words:
        return []
    by_name, by_text = [], []
    for summary in session_summaries(subject_slug, log_dir):
        name = (summary.title or summary.first_message).lower()
        if all(w in name for w in words):
            by_name.append(SessionMatch(summary, True, None))
            continue
        text = _searchable_text(
            Session(subject_slug, summary.id, record_dir(subject_slug), log_dir)
        )
        haystack = f"{name}\n{text.lower()}"
        if all(w in haystack for w in words):
            by_text.append(SessionMatch(summary, False, _snippet(text, words)))
    return by_name + by_text


def _searchable_text(session: Session) -> str:
    """The log as plain-ish text: no properties or heading, no markdown marks, one line."""
    try:
        text = session.log_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = session.rendered_text()
    text = _LOG_PROPERTIES.sub("", text, count=1)
    text = re.sub(r"\A\s*# .*\n", "", text)  # "# Subject — date (n)"
    text = re.sub(r"^---$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\*Session (?:ended|resumed)\b.*\*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^> ?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[!\w+\][ \t]*", "", text)
    text = text.replace("**", "")
    return " ".join(text.split())


def _snippet(text: str, words: list[str]) -> str:
    """About a line of `text` around the first match, cut at spaces and never inside math."""
    lower = text.lower()
    found = [(lower.find(w), w) for w in words if lower.find(w) >= 0]
    if not found:
        return text[: SNIPPET_BEFORE + SNIPPET_AFTER]
    pos, word = min(found)
    start = max(0, pos - SNIPPET_BEFORE)
    end = min(len(text), pos + len(word) + SNIPPET_AFTER)
    if start > 0:
        space = text.rfind(" ", 0, start)
        start = space + 1 if space >= 0 else 0
    if end < len(text):
        space = text.find(" ", end)
        end = space if space >= 0 else len(text)
    for m in _MATH.finditer(text):
        if m.start() < start < m.end():
            start = m.start()
        if m.start() < end < m.end():
            end = m.end()
    prefix = "… " if start > 0 else ""
    suffix = " …" if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


# -- cleaning up empty sessions (decided 2026-11-10) -------------------------------------


@dataclass(frozen=True)
class Cleanup:
    """A session the cleanup finds, and whether it would be removed."""

    session_id: str
    started: str | None
    reason: str  # "empty", or "wrap-up only" (the learner only sent /end-session)
    kept: str | None  # why it is kept instead, or None if it would be removed


def cleanup_reason(session: Session) -> str | None:
    if session.empty:
        return "empty"
    learner = [e.text for e in session.events() if e.role == "learner"]
    if learner and all(text.split()[:1] == ["/end-session"] for text in learner):
        return "wrap-up only"
    return None


def cleanup_candidates(subject_slug: str, log_dir: Path) -> list[Cleanup]:
    """Empty and wrap-up-only sessions, newest first. The open session is never listed;
    one the learner wrote in, or that a later session continues, is listed as kept."""
    records = record_dir(subject_slug)
    ids = [p.stem for p in records.glob("*.jsonl") if SESSION_ID.match(p.stem)]
    sessions = [Session(subject_slug, i, records, log_dir) for i in ids]
    continued = {s.continues for s in sessions if s.continues}
    active = active_session_id(subject_slug)
    out = []
    for session in sorted(sessions, key=lambda s: session_order(s.id), reverse=True):
        reason = cleanup_reason(session)
        if reason is None or session.id == active:
            continue
        events = session.events()
        if not session._log_untouched() or any(e.kind == "annotation" for e in events):
            kept = "you wrote in its log"
        elif session.id in continued:
            kept = "a later session continues it"
        else:
            kept = None
        start = next((e for e in events if e.kind == "start"), None)
        out.append(Cleanup(session.id, start.ts if start else None, reason, kept))
    return out


def remove_sessions(subject_slug: str, log_dir: Path, session_ids: list[str]) -> int:
    """Remove the given sessions, each only if the cleanup would still remove it now (the
    list the learner saw may be out of date). Returns how many were removed."""
    removable = {
        c.session_id
        for c in cleanup_candidates(subject_slug, log_dir)
        if c.kept is None
    }
    removed = 0
    for session_id in session_ids:
        if session_id in removable:
            Session(
                subject_slug, session_id, record_dir(subject_slug), log_dir
            ).remove()
            removable.discard(session_id)
            removed += 1
    return removed


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

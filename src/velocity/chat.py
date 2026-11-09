"""Web chats: at most one open chat per subject, each driving one tutor engine.

Decisions (M8 design review, 2026-11-06):
- One open session per subject across all front ends: a chat doesn't start while another
  session of the subject is open (velocity tutor, velocity chat, another chat), unless the
  learner closes that one (e.g. left over after a crash).
- Pause stops the engine without a wrap-up, so the session can be resumed. A chat idle
  for 30 minutes, and every chat when the server stops, is paused the same way.
- Resume reopens a paused session: through Claude Code's own `--resume` when the session
  has a Claude Code conversation, else by reopening the log and having the tutor read it.
- Continue starts a new session linked to a finished one.
"""

import asyncio
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from velocity.claude_engine import MODELS, ChatEvent, ClaudeCodeChat, EngineError
from velocity.config import Config, Subject
from velocity.hooks import CONTINUE_ENV, REOPEN_ENV
from velocity.session import (
    SESSION_ID,
    Session,
    active_session_id,
    record_dir,
    set_active,
)
from velocity.workspace import build_workspace

IDLE_TIMEOUT = 30 * 60  # seconds


class ChatError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class OpenChat:
    subject: Subject
    engine: ClaudeCodeChat
    model: str
    last_activity: float
    busy: bool = False
    session_id: str | None = None  # the velocity session, once the hooks have opened it


# (workspace, model, Claude Code session to resume, environment) -> engine
EngineFactory = Callable[..., ClaudeCodeChat]


def default_engine(workspace, model, resume_id, env) -> ClaudeCodeChat:
    return ClaudeCodeChat(workspace, model=model, resume_id=resume_id, env=env)


class ChatManager:
    def __init__(
        self,
        config: Config,
        engine_factory: EngineFactory = default_engine,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.engine_factory = engine_factory
        self.clock = clock
        self.chats: dict[str, OpenChat] = {}

    # -- status ----------------------------------------------------------------------

    def status(self, subject: Subject) -> dict:
        """Whether a chat is open, and the conversation so far (to reopen the page)."""
        chat = self.chats.get(subject.slug)
        session_id = active_session_id(subject.slug) or (
            chat.session_id if chat else None
        )
        session = _session(subject, session_id) if session_id else None
        return {
            "open": chat is not None,
            "model": chat.model if chat else None,
            "busy": bool(chat and chat.busy),
            "session": session_id,
            "title": session.title if session else None,
            # Open in another front end (or left over from a crash), not in this server.
            "elsewhere": session_id is not None and chat is None,
            "messages": conversation(session) if session and chat else [],
        }

    # -- lifecycle -------------------------------------------------------------------

    async def start(
        self,
        subject: Subject,
        model: str = "sonnet",
        resume: str | None = None,
        continues: str | None = None,
        close_other: bool = False,
    ) -> None:
        if model not in MODELS:
            raise ChatError(f"unknown model {model!r}")
        if subject.slug in self.chats:
            raise ChatError("A chat is already open for this subject.", 409)
        other = active_session_id(subject.slug)
        if other:
            if not close_other:
                raise ChatError(
                    f"Session {other} of {subject.name} is still open, in velocity tutor, "
                    "velocity chat or another window. End it there first, or close it here "
                    "if nothing is using it anymore.",
                    409,
                )
            _session(subject, other).end()
            set_active(subject.slug, None)

        env = dict(os.environ)
        env.pop(CONTINUE_ENV, None)
        env.pop(REOPEN_ENV, None)
        resume_id = None
        if resume:
            session = _existing(subject, resume)
            if not session.ended or session.wrapped_up:
                raise ChatError(
                    f"Session {resume} can't be resumed: only paused sessions can "
                    "(a wrapped-up session can be continued instead)."
                )
            resume_id = claude_conversation(subject, resume)
            if resume_id is None:
                env[REOPEN_ENV] = resume
        if continues:
            if not _existing(subject, continues).ended:
                raise ChatError(f"Session {continues} is still open.")
            env[CONTINUE_ENV] = continues

        workspace = build_workspace(self.config, subject)
        try:
            engine = self.engine_factory(workspace, model, resume_id, env)
            await engine.start()
        except (EngineError, OSError) as e:
            raise ChatError(str(e), 500) from None
        # A resumed session is known now; Claude Code's SessionStart hook marks it active
        # only a moment later, and the page wants its conversation right away.
        self.chats[subject.slug] = OpenChat(
            subject, engine, model, self.clock(), session_id=resume
        )

    async def send(self, subject: Subject, text: str) -> AsyncIterator[ChatEvent]:
        chat = self.chats.get(subject.slug)
        if chat is None:
            raise ChatError("No chat is open for this subject.", 409)
        chat.busy = True
        chat.last_activity = self.clock()
        try:
            async for event in chat.engine.send(text):
                yield event
        finally:
            chat.busy = False
            chat.last_activity = self.clock()
            chat.session_id = active_session_id(subject.slug) or chat.session_id
        if not chat.engine.running:
            self.chats.pop(subject.slug, None)
            yield ChatEvent("ended", text="crashed")
        elif chat.session_id and _session(subject, chat.session_id).wrapped_up:
            # /end-session finished: the hooks closed the log, so stop the engine.
            await self.pause(subject)
            yield ChatEvent("ended", text="wrapped-up")

    async def pause(self, subject: Subject) -> None:
        """Stop the engine without a wrap-up; the session can be resumed later."""
        chat = self.chats.pop(subject.slug, None)
        if chat:
            await chat.engine.stop()

    async def pause_idle(self) -> list[str]:
        now = self.clock()
        idle = [
            slug
            for slug, chat in self.chats.items()
            if not chat.busy and now - chat.last_activity > IDLE_TIMEOUT
        ]
        for slug in idle:
            await self.pause(self.chats[slug].subject)
        return idle

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(self.pause(c.subject) for c in list(self.chats.values()))
        )


def _session(subject: Subject, session_id: str) -> Session:
    return Session(
        subject.slug, session_id, record_dir(subject.slug), subject.sessions_dir
    )


def _existing(subject: Subject, session_id: str) -> Session:
    session = _session(subject, session_id) if SESSION_ID.match(session_id) else None
    if session is None or not session.record_path.exists():
        raise ChatError(f"No session {session_id!r}.", 404)
    return session


def claude_conversation(subject: Subject, session_id: str) -> str | None:
    """The latest Claude Code conversation attached to a velocity session, if any.
    (The SessionStart hook maps each Claude Code session id to its velocity session.)"""
    mapping = record_dir(subject.slug) / "claude-sessions"
    if not mapping.is_dir():
        return None
    matches = [p for p in mapping.iterdir() if p.read_text().strip() == session_id]
    return max(matches, key=lambda p: p.stat().st_mtime).name if matches else None


def conversation(session: Session) -> list[dict]:
    """The session's dialogue so far, for redrawing the chat after a reload."""
    out = []
    for e in sorted(session.events(), key=lambda e: e.ts):
        if e.role in ("learner", "tutor"):
            out.append({"role": e.role, "text": e.text})
        elif e.role == "block":
            out.append({"role": "block", "text": e.text, "title": e.title or ""})
    return out

"""Web chats: at most one open chat per subject, each driving one tutor engine: Claude Code
headless (claude_engine.py) or the own agent loop (api_engine.py).

Decisions (M8 design review, 2026-11-06; engines 2026-11-09):
- One open session per subject across all front ends: a chat doesn't start while another
  session of the subject is open (velocity tutor, velocity chat, another chat), unless the
  learner closes that one (e.g. left over after a crash).
- Pause stops the engine without a wrap-up, so the session can be resumed. A chat idle
  for 30 minutes, and every chat when the server stops, is paused the same way.
- Resume reopens a paused session. On Claude Code: through its own `--resume` when the
  session has a Claude Code conversation, else by reopening the log and having the tutor
  read it. On the own loop: by rebuilding the conversation from the session record, for
  sessions from any front end.
- Continue starts a new session linked to a finished one.
- Each web start and resume records its engine and model in the session record, so Resume
  can default to whatever ran the session last.
"""

import asyncio
import os
import shutil
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from velocity import api_engine, claude_engine
from velocity.agent import MODEL_ALIASES, recorded_cost
from velocity.api_engine import ApiChat, api_available
from velocity.claude_engine import ClaudeCodeChat
from velocity.config import Config, Subject
from velocity.engine import (
    CLAUDE_CODE,
    OWN_LOOP,
    STOPPED_MARK,
    ChatEvent,
    Engine,
    EngineError,
)
from velocity.hooks import CONTINUE_ENV, REMINDER_SHOWN_ENV, REOPEN_ENV
from velocity.session import (
    SESSION_ID,
    Session,
    active_session_id,
    record_dir,
    set_active,
)
from velocity.workspace import build_workspace

IDLE_TIMEOUT = 30 * 60  # seconds
MODELS = {CLAUDE_CODE: claude_engine.MODELS, OWN_LOOP: api_engine.MODELS}


class ChatError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class OpenChat:
    subject: Subject
    engine: Engine
    engine_name: str  # CLAUDE_CODE or OWN_LOOP
    model: str
    last_activity: float
    busy: bool = False
    session_id: str | None = None  # the velocity session, once the hooks have opened it
    engine_recorded: bool = False


# Claude Code: (workspace, model, Claude Code session to resume, environment) -> engine
EngineFactory = Callable[..., Engine]
# Own loop: (config, subject, model, session to reopen, session it continues) -> engine
ApiEngineFactory = Callable[..., Engine]


def default_engine(workspace, model, resume_id, env) -> ClaudeCodeChat:
    return ClaudeCodeChat(workspace, model=model, resume_id=resume_id, env=env)


def default_api_engine(config, subject, model, reopen, continues) -> ApiChat:
    return ApiChat(config, subject, model, reopen=reopen, continues=continues)


def engines() -> dict:
    """The engines, their models, and whether each can run here (and if not, why)."""
    return {
        CLAUDE_CODE: {
            "label": "Claude Code (your plan)",
            "models": claude_engine.MODELS,
            "available": shutil.which("claude") is not None,
            "reason": "Claude Code (`claude`) is not on PATH.",
        },
        OWN_LOOP: {
            "label": "API (billed per token)",
            "models": api_engine.MODELS,
            "available": api_available(),
            "reason": f"{api_engine.API_KEY_ENV} is not set in the terminal that runs "
            "velocity web.",
        },
    }


class ChatManager:
    def __init__(
        self,
        config: Config,
        engine_factory: EngineFactory = default_engine,
        clock: Callable[[], float] = time.monotonic,
        api_engine_factory: ApiEngineFactory = default_api_engine,
    ) -> None:
        self.config = config
        self.engine_factory = engine_factory
        self.api_engine_factory = api_engine_factory
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
        on_api = chat is not None and chat.engine_name == OWN_LOOP
        return {
            "open": chat is not None,
            "engine": chat.engine_name if chat else None,
            "model": chat.model if chat else None,
            # The session's own-loop cost so far (no dollar amounts on Claude Code).
            "cost": recorded_cost(session) if on_api and session else None,
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
        engine: str = CLAUDE_CODE,
    ) -> None:
        if engine not in MODELS:
            raise ChatError(f"unknown engine {engine!r}")
        if model not in MODELS[engine]:
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

        if resume:
            session = _existing(subject, resume)
            if not session.ended or session.wrapped_up:
                raise ChatError(
                    f"Session {resume} can't be resumed: only paused sessions can "
                    "(a wrapped-up session can be continued instead)."
                )
        if continues and not _existing(subject, continues).ended:
            raise ChatError(f"Session {continues} is still open.")

        try:
            if engine == OWN_LOOP:
                chat_engine = self.api_engine_factory(
                    self.config, subject, model, resume, continues
                )
            else:
                chat_engine = self._claude_engine(subject, model, resume, continues)
            await chat_engine.start()
        except (EngineError, OSError) as e:
            raise ChatError(str(e), 500) from None
        # A resumed session is known now; Claude Code's SessionStart hook marks it active
        # only a moment later, and the page wants its conversation right away.
        chat = OpenChat(
            subject,
            chat_engine,
            engine,
            model,
            self.clock(),
            session_id=resume or active_session_id(subject.slug),
        )
        self.chats[subject.slug] = chat
        self._record_engine(chat)

    def _claude_engine(
        self, subject: Subject, model: str, resume: str | None, continues: str | None
    ) -> Engine:
        env = dict(os.environ)
        env.pop(CONTINUE_ENV, None)
        env.pop(REOPEN_ENV, None)
        env[REMINDER_SHOWN_ENV] = "1"  # the page shows the third-party reminder
        resume_id = None
        if resume:
            # Claude Code's own conversation is complete only if Claude Code ran the
            # session last; after turns on the own loop, reopen and read the log instead.
            session = _session(subject, resume)
            if last_engine(subject, session)["engine"] == CLAUDE_CODE:
                resume_id = claude_conversation(subject, resume)
            if resume_id is None:
                env[REOPEN_ENV] = resume
        if continues:
            env[CONTINUE_ENV] = continues
        workspace = build_workspace(self.config, subject)
        return self.engine_factory(workspace, model, resume_id, env)

    def _record_engine(self, chat: OpenChat) -> None:
        """Note in the session record which engine and model run this sitting (once the
        session is open; Claude Code's SessionStart hook opens or reopens it a moment
        after the process starts, so this is tried again after each message)."""
        if chat.engine_recorded or not chat.session_id:
            return
        session = _session(chat.subject, chat.session_id)
        if session.ended:
            return
        session.append(
            "event",
            chat.engine_name,
            kind="engine",
            meta={"engine": chat.engine_name, "model": chat.model},
        )
        chat.engine_recorded = True

    async def send(self, subject: Subject, text: str) -> AsyncIterator[ChatEvent]:
        chat = self.chats.get(subject.slug)
        if chat is None:
            raise ChatError("No chat is open for this subject.", 409)
        chat.busy = True
        chat.last_activity = self.clock()
        streamed = []
        try:
            async for event in chat.engine.send(text):
                if event.type == "text":
                    streamed.append(event.text)
                elif event.type == "stopped" and chat.engine_name == CLAUDE_CODE:
                    self._log_stopped(subject, "".join(streamed))
                yield event
        finally:
            chat.busy = False
            chat.last_activity = self.clock()
            chat.session_id = active_session_id(subject.slug) or chat.session_id
            self._record_engine(chat)
        if not chat.engine.running:
            self.chats.pop(subject.slug, None)
            yield ChatEvent("ended", text="crashed")
        elif chat.session_id and _session(subject, chat.session_id).wrapped_up:
            # /end-session finished: the hooks closed the log, so stop the engine.
            await self.pause(subject)
            yield ChatEvent("ended", text="wrapped-up")

    async def stop_reply(self, subject: Subject) -> None:
        """Stop the reply being streamed; what the tutor said so far is kept."""
        chat = self.chats.get(subject.slug)
        if chat and chat.busy:
            await chat.engine.interrupt()

    @staticmethod
    def _log_stopped(subject: Subject, partial: str) -> None:
        """Log a stopped Claude Code reply (its Stop hook doesn't run on an interrupt;
        the own loop logs its own)."""
        session_id = active_session_id(subject.slug)
        if not session_id:
            return
        session = _session(subject, session_id)
        if session.ended:
            return
        partial = partial.strip()
        session.append("tutor", (partial + "\n\n" if partial else "") + STOPPED_MARK)
        session.render()

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


def last_engine(subject: Subject, session: Session) -> dict:
    """The engine and model that ran the session last, the default for resuming it.

    Web chats record theirs. Older sessions and terminal ones are inferred: a Claude Code
    conversation means velocity tutor; otherwise it was velocity chat, whose turns record
    their model with their usage. The model is None when nothing says which it was.
    """
    engine, model = None, None
    for e in session.events():
        meta = e.meta or {}
        if e.kind == "engine":
            engine, model = meta.get("engine"), meta.get("model")
        elif e.kind == "usage":
            alias = {v: k for k, v in MODEL_ALIASES.items()}.get(meta.get("model"))
            engine, model = OWN_LOOP, alias
    if engine is None:
        has_claude = claude_conversation(subject, session.id) is not None
        engine = CLAUDE_CODE if has_claude else OWN_LOOP
    return {"engine": engine, "model": model}


def conversation(session: Session) -> list[dict]:
    """The session's dialogue so far, for redrawing the chat after a reload."""
    out = []
    for e in sorted(session.events(), key=lambda e: e.ts):
        if e.role in ("learner", "tutor"):
            out.append({"role": e.role, "text": e.text})
        elif e.role == "block":
            out.append({"role": "block", "text": e.text, "title": e.title or ""})
    return out

"""Claude Code hook handlers. Each reads the hook's JSON from stdin.

session-start  open a new session log, or reattach on resume; give the tutor its context
user-prompt    log the learner's message verbatim
stop           log the tutor's final message verbatim
session-end    close the log ("ended without wrap-up" unless already ended)
guard          PreToolUse: refuse file edits inside the vault
tool-use       PostToolUse: record the tutor's tool call (name and input) in the session record
"""

import json
import os
from datetime import date
from pathlib import Path
from typing import TextIO

from velocity.config import Config, Subject
from velocity.session import (
    ENDED_WITH_WRAP_UP,
    SESSION_ID,
    Session,
    active_session_id,
    record_dir,
    set_active,
)
from velocity.state import StateError, load_state


def _claude_map(subject: Subject) -> Path:
    return record_dir(subject.slug) / "claude-sessions"


def _session(subject: Subject, session_id: str) -> Session:
    return Session(
        subject.slug, session_id, record_dir(subject.slug), subject.sessions_dir
    )


def _active(subject: Subject) -> Session | None:
    session_id = active_session_id(subject.slug)
    return _session(subject, session_id) if session_id else None


# Set by `velocity tutor --continue <id>` for the SessionStart hook Claude Code runs.
CONTINUE_ENV = "VELOCITY_CONTINUE"


def _continues(subject: Subject, payload: dict) -> str | None:
    """The finished session a new session should continue, if one was asked for."""
    session_id = payload.get("continues") or os.environ.get(CONTINUE_ENV, "")
    if not SESSION_ID.match(session_id):
        return None
    earlier = _session(subject, session_id)
    return session_id if earlier.record_path.exists() and earlier.ended else None


# Set by the web UI to resume a paused session that has no Claude Code conversation to
# resume (e.g. one from `velocity chat`): the session reopens, and the tutor reads it.
REOPEN_ENV = "VELOCITY_REOPEN"


def _reopen(subject: Subject, payload: dict) -> Session | None:
    session_id = payload.get("reopen") or os.environ.get(REOPEN_ENV, "")
    if not SESSION_ID.match(session_id):
        return None
    session = _session(subject, session_id)
    if not session.record_path.exists() or not session.ended or session.wrapped_up:
        return None
    session.reopen()
    return session


# Set by the web UI: it shows the third-party reminder itself where sessions start, and
# shows sources in a reader beside the chat.
WEB_UI_ENV = "VELOCITY_WEB_UI"

WEB_READER_CONTEXT = (
    "The learner uses velocity's web page, which shows the sources in a reader beside "
    "the chat. Your citations (`<source> §<id>`) and page references (`PDF p. <n>`) "
    "become links that open the reader there, so cite in exactly those forms and tell "
    "the learner they can click them; you can't move the reader yourself. A message "
    "starting 'About <source> PDF p. <n> (§<id>):' is about the page the learner has open."
)


def _web_ui(payload: dict) -> bool:
    return bool(payload.get("web_ui")) or os.environ.get(WEB_UI_ENV) == "1"


def session_start(config: Config, subject: Subject, payload: dict) -> dict:
    claude_id = payload.get("session_id", "")
    mapping = _claude_map(subject) / claude_id if claude_id else None
    session = None
    if payload.get("source") in ("resume", "compact") and mapping and mapping.exists():
        session = _session(subject, mapping.read_text().strip())
        if session.wrapped_up:
            session = (
                None  # wrapped up with /end-session: a resumed chat starts a new log
            )
        elif session.ended:
            session.reopen()
    reopened = session is None and (session := _reopen(subject, payload)) is not None
    resumed = session is not None
    if session is None:
        session = Session.start(
            subject.slug,
            subject.name,
            record_dir(subject.slug),
            subject.sessions_dir,
            sources=[s.name for s in subject.sources],
            continues=_continues(subject, payload),
        )
    if mapping:
        mapping.parent.mkdir(parents=True, exist_ok=True)
        mapping.write_text(session.id)
    set_active(subject.slug, session.id)

    sources = (
        ", ".join(s.name for s in subject.sources)
        or "none (teach from general knowledge)"
    )
    previous = sorted(
        (
            p.stem
            for p in record_dir(subject.slug).glob("*.jsonl")
            if p.stem != session.id
        ),
        key=lambda i: (i.rsplit("-", 1)[0], int(i.rsplit("-", 1)[1])),
    )
    status = "resumed" if resumed else "new session"
    from velocity.notes import list_notes

    note_count = len(list_notes(config, subject)) if subject.note_folders else 0
    notes_line = (
        f"Learner notes: {note_count} readable notes (list_notes / read_note; read-only)."
        if subject.note_folders
        else "Learner notes: no note folders configured."
    )
    context = [
        f"velocity session {session.id} for subject '{subject.name}' ({status}).",
        f"Sources: {sources}.",
        notes_line,
        f"Previous sessions: {', '.join(previous[-5:]) or 'none'}"
        + (" (use read_session to review the last one)." if previous else "."),
        "The conversation log is at " + session.log_path.as_posix() + ".",
    ]
    if reopened and payload.get("history"):
        # The own loop rebuilt the conversation from the record (history.py).
        context.append(
            "This session was paused and is resumed here. Its earlier conversation is "
            "restored above, except tool results, which aren't kept: read a section or "
            "note again if you need its text. Pick up where it left off."
        )
    elif reopened:
        context.append(
            "This session was paused and is resumed here without its earlier conversation. "
            f"Read it with read_session('{session.id}') first, then pick up where it left off."
        )
    if session.title:
        context.append(f"Session title so far: {session.title}")
    else:
        context.append(
            "This session has no title yet: give it one with set_session_title once its "
            "topic is clear."
        )
    if session.continues:
        earlier = _session(subject, session.continues)
        context.append(
            f"This session continues session {earlier.id}"
            + (f" ({earlier.title})" if earlier.title else "")
            + ". Before teaching, read it with read_session and pick up where it left off."
            + (f"\nIts recap:\n{earlier.recap()}" if earlier.recap() else "")
        )
    elif previous:
        recap = _session(subject, previous[-1]).recap()
        if recap:
            context.append(f"Recap of the last session ({previous[-1]}):\n{recap}")
    try:
        context.append(load_state(subject.dir).summary(date.today()))
    except StateError as e:
        context.append(f"Learner state could not be read: {e}")
    web_ui = _web_ui(payload)
    if web_ui and subject.sources:
        context.append(WEB_READER_CONTEXT)
    if not resumed and web_ui:
        context.append(
            "The learner has already seen the third-party reminder on the page: don't "
            "repeat it. Open with the rest of the start of session from your instructions."
        )
    elif not resumed:
        context.append("Open with the third-party reminder from your instructions.")
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(context),
        }
    }


def _log_changes(session: Session) -> str:
    """Report log annotations and deletions the tutor hasn't been told about yet.

    Reported items are saved in the record, so each is reported once and annotations stay
    available to read_session even if the learner later deletes them from the markdown.
    """
    if not session.log_path.exists():
        return ""
    check = session.check_log()
    seen = {
        (e.kind, e.text)
        for e in session.events()
        if e.kind in ("annotation", "missing")
    }
    new_annotations = [a for a in check.annotations if ("annotation", a) not in seen]
    new_missing = [m for m in check.missing if ("missing", m) not in seen]
    for a in new_annotations:
        session.append("event", a, kind="annotation")
    for m in new_missing:
        session.append("event", m, kind="missing")
    return log_changes_context(new_annotations, new_missing)


def log_changes_context(new_annotations: list[str], new_missing: list[str]) -> str:
    """The message that tells the tutor about log annotations and missing text."""
    parts = []
    if new_annotations:
        parts.append(
            "The learner added these annotations to the session log (their own thoughts; "
            "respond to them if relevant):\n"
            + "\n".join(f"- {a}" for a in new_annotations)
        )
    if new_missing:
        parts.append(
            "Text is missing from the session log (deleted or edited by the learner). Mention it "
            "and ask before calling restore_log:\n"
            + "\n".join(f"- {m}" for m in new_missing)
        )
    return "\n\n".join(parts)


def user_prompt(config: Config, subject: Subject, payload: dict) -> dict:
    session = _active(subject)
    if not session or session.ended or not payload.get("prompt", "").strip():
        return {}
    changes = _log_changes(session)
    dialogue = [e for e in session.events() if e.role in ("learner", "tutor")]
    retried = (
        dialogue
        and dialogue[-1].role == "learner"
        and dialogue[-1].text == payload["prompt"]
    )
    # A message sent again after a failed reply (Retry) is logged once.
    if not retried:
        session.append("learner", payload["prompt"])
        session.render()
    if not changes:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": changes,
        }
    }


def stop(config: Config, subject: Subject, payload: dict) -> dict:
    session = _active(subject)
    message = (payload.get("last_assistant_message") or "").strip()
    if not session or session.ended:
        return {}
    if message:
        session.append("tutor", message)
        session.render()
    if session.wrap_up_requested:
        session.end(ENDED_WITH_WRAP_UP)
        set_active(subject.slug, None)
    return {}


def session_end(config: Config, subject: Subject, payload: dict) -> dict:
    session = _active(subject)
    # Leaving to resume or clear is not the end of the learning session.
    if session and payload.get("reason") not in ("resume",):
        session.render()
        session.end()
        set_active(subject.slug, None)
    return {}


MCP_TOOL_PREFIX = "mcp__velocity__"


def tool_use(config: Config, subject: Subject, payload: dict) -> dict:
    """Record a tool call in the session record, so a session can be replayed with them.

    Only the name and input are kept: results can contain source text, which must not be
    copied into records. Tool calls never appear in the markdown log. Claude Code's MCP tool
    names lose their `mcp__velocity__` prefix, and its Skill tool is recorded as
    `load_skill`, the name the own agent loop uses, so records from both front ends match.
    """
    session = _active(subject)
    name = payload.get("tool_name", "")
    if not session or session.ended or not name:
        return {}
    tool_input = payload.get("tool_input") or {}
    if name == "Skill":
        # The skill's name; the field isn't documented, so accept the likely ones.
        skill = next(
            (tool_input[k] for k in ("skill", "command", "name") if tool_input.get(k)),
            "",
        )
        name, tool_input = "load_skill", {"name": str(skill).lstrip("/")}
    name = name.removeprefix(MCP_TOOL_PREFIX)
    meta = {"input": tool_input}
    if payload.get("is_error"):
        meta["error"] = True
    session.append("event", name, kind="tool", meta=meta)
    return {}


def guard(config: Config, subject: Subject, payload: dict) -> dict:
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return {}
    path = Path(raw)
    if not path.is_absolute():
        path = Path(payload.get("cwd") or ".") / path
    if path.resolve().is_relative_to(config.vault_path):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "The tutor never writes in the learner's vault. Their notes are theirs; "
                    "session logs are written automatically. Use append_log for log content."
                ),
            }
        }
    return {}


HANDLERS = {
    "session-start": session_start,
    "user-prompt": user_prompt,
    "stop": stop,
    "session-end": session_end,
    "guard": guard,
    "tool-use": tool_use,
}


def run(
    event: str, config: Config, subject: Subject, stdin: TextIO, stdout: TextIO
) -> None:
    payload = json.loads(stdin.read() or "{}")
    result = HANDLERS[event](config, subject, payload)
    if result:
        stdout.write(json.dumps(result))

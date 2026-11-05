"""Claude Code hook handlers. Each reads the hook's JSON from stdin.

session-start  open a new session log, or reattach on resume; give the tutor its context
user-prompt    log the learner's message verbatim
stop           log the tutor's final message verbatim
session-end    close the log ("ended without wrap-up" unless already ended)
guard          PreToolUse: refuse file edits inside the vault
"""

import json
from datetime import date
from pathlib import Path
from typing import TextIO

from velocity.config import Config, Subject
from velocity.session import (
    ENDED_WITH_WRAP_UP,
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
    resumed = session is not None
    if session is None:
        session = Session.start(
            subject.slug,
            subject.name,
            record_dir(subject.slug),
            subject.sessions_dir,
            sources=[s.name for s in subject.sources],
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
    context = [
        f"velocity session {session.id} for subject '{subject.name}' ({status}).",
        f"Sources: {sources}.",
        f"Previous sessions: {', '.join(previous[-5:]) or 'none'}"
        + (" (use read_session to review the last one)." if previous else "."),
        "The conversation log is at " + session.log_path.as_posix() + ".",
    ]
    if previous:
        recap = _session(subject, previous[-1]).recap()
        if recap:
            context.append(f"Recap of the last session ({previous[-1]}):\n{recap}")
    try:
        context.append(load_state(subject.dir).summary(date.today()))
    except StateError as e:
        context.append(f"Learner state could not be read: {e}")
    if not resumed:
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
}


def run(
    event: str, config: Config, subject: Subject, stdin: TextIO, stdout: TextIO
) -> None:
    payload = json.loads(stdin.read() or "{}")
    result = HANDLERS[event](config, subject, payload)
    if result:
        stdout.write(json.dumps(result))

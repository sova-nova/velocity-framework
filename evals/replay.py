"""Replay one case: rebuild the session up to the learner's message, run one tutor turn.

The turn runs on the real `velocity chat` agent and the real tools (through an in-process
MCP server), in a throwaway vault. XDG_DATA_HOME must point to a sandbox data directory
(the runner sets it), because session records live there.
"""

import io
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from mcp import Client

from evals.cases import DATA_DIR, ReplayInput, Turn, state_before
from velocity import history, hooks
from velocity.agent import (
    DEFAULT_EFFORT,
    StreamFactory,
    TutorAgent,
    UsageByModel,
    mcp_tool_caller,
    mcp_tool_params,
)
from velocity.config import Config, SourceRef, Subject
from velocity.history import HistoryBuilder
from velocity.mcp_server import build_server
from velocity.session import Session, active_session_id, record_dir
from velocity.skills import load_skills
from velocity.state import save_state
from velocity.tools import ToolError, TutorTools


class SkipCase(Exception):
    """The case can't run here (e.g. its source PDF isn't available)."""


@dataclass
class ReplayResult:
    reply: str
    tool_log: list[tuple[str, dict]]
    usage: UsageByModel
    transcript: str = ""  # what the agent printed: streamed text and tool calls
    messages_before: int = 0
    extra: dict = field(default_factory=dict)


def _no_ocr(*args) -> dict[int, str]:
    raise ToolError(
        "This part of the source is not in the OCR cache, and eval runs don't run OCR. "
        "Continue without it."
    )


def block_note(turn: Turn) -> str:
    """How a block the tutor added to the log appears in its own message history."""
    return history.block_note(turn.title, turn.text)


async def replay(
    inp: ReplayInput,
    *,
    sandbox: Path,
    stream: StreamFactory,
    model: str,
    effort: str = DEFAULT_EFFORT,
    source_paths: dict[str, Path],
    today: date | None = None,
    data_dir: Path = DATA_DIR,
) -> ReplayResult:
    today = today or date.today()
    missing = [s for s in inp.sources if s not in source_paths]
    if missing:
        raise SkipCase(
            f"source {', '.join(missing)} not available (use --source NAME=PDF)"
        )

    # A fresh slug per run, so runs in parallel never share records or state.
    slug = f"{inp.subject_slug}-{uuid.uuid4().hex[:8]}"
    vault = sandbox / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    config = Config(vault.resolve())
    subject_dir = config.tutor_dir / slug

    note_folders = set()
    for rel, text in inp.notes.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        note_folders.add(path.parent.resolve())
    subject = Subject(
        slug,
        inp.subject_name,
        subject_dir,
        "",
        tuple(sorted(note_folders)),
        tuple(SourceRef(source_paths[s], s) for s in inp.sources),
    )
    subject.sessions_dir.mkdir(parents=True, exist_ok=True)

    records = record_dir(slug)
    records.mkdir(parents=True, exist_ok=True)
    for session_id in inp.previous_sessions:
        shutil.copy(
            data_dir / "records" / inp.subject_slug / f"{session_id}.jsonl",
            records / f"{session_id}.jsonl",
        )
    state = state_before(
        inp.previous_sessions,
        inp.date,
        today,
        (data_dir / "state.json").read_text(encoding="utf-8"),
        (inp.session_id, inp.session_questions, inp.session_threads),
    )
    save_state(state, subject_dir, subject.name, today)

    tools = TutorTools(config, subject, ocr_factory=lambda: _no_ocr)
    out = io.StringIO()
    async with Client(build_server(tools)) as mcp:
        agent = TutorAgent(
            config=config,
            subject=subject,
            stream=stream,
            call_tool=mcp_tool_caller(mcp),
            tools=mcp_tool_params((await mcp.list_tools()).tools),
            skills=load_skills(),
            out=out,
            model=model,
            effort=effort,
        )
        agent.start()
        _seed_history(agent, slug, subject, inp.history, tools)
        if inp.annotations:
            # The learner typed these into the log; the user-prompt hook finds them.
            session = _active_session(slug, subject)
            with session.log_path.open("a", encoding="utf-8") as f:
                f.write("\n\n".join(inp.annotations))
        before = len(agent.messages)
        reply = await agent.turn(inp.message)
        agent.finish()
    return ReplayResult(reply, agent.tool_log, agent.usage, out.getvalue(), before)


def _active_session(slug: str, subject: Subject) -> Session:
    return Session(
        slug, active_session_id(slug), record_dir(slug), subject.sessions_dir
    )


# Recorded calls that are run again while seeding, so the learner state matches.
STATE_TOOLS = {"record_question", "set_topic_status", "open_thread", "close_thread"}


def _seed_history(
    agent: TutorAgent,
    slug: str,
    subject: Subject,
    history: tuple[Turn, ...],
    tools: TutorTools,
):
    """Put the earlier part of the session into the agent's history, the session record and
    the markdown log, the way it would be after those turns had run. Recorded calls that
    change learner state are run again, so the state matches too."""
    session = _active_session(slug, subject)
    builder = HistoryBuilder(agent.messages)
    annotations: list[str] = []
    for turn in history:
        if turn.role == "annotation":
            session.render()
            with session.log_path.open("a", encoding="utf-8") as f:
                f.write(turn.text.strip() + "\n\n")
            session.append("event", turn.text.strip(), kind="annotation")
            annotations.append(turn.text.strip())
        elif turn.role == "learner":
            session.append("learner", turn.text)
            # Context the agent would have added with this message: the session start
            # context (first message only), then log annotations.
            context = list(agent._pending_context)
            agent._pending_context.clear()
            if annotations:
                context.append(hooks.log_changes_context(annotations, []))
                annotations = []
            builder.learner(turn.text, context)
        elif turn.role == "block":
            session.append("block", turn.text, title=turn.title, kind="example")
            builder.block(turn.text, turn.title)
        elif turn.role == "tool":
            # The call as the tutor made it. The result wasn't recorded (it can hold
            # source text), so the tutor sees a placeholder.
            session.append(
                "event", turn.text, kind="tool", meta={"input": turn.args or {}}
            )
            if turn.text in STATE_TOOLS:
                try:
                    getattr(tools, turn.text)(**(turn.args or {}))
                except (ToolError, TypeError):
                    pass  # it failed (or was malformed) the first time too
            builder.tool(turn.text, turn.args or {})
        elif turn.role == "tutor":
            session.append("tutor", turn.text)
            builder.tutor(turn.text)
        else:
            raise ValueError(f"unknown history role {turn.role!r}")
    session.render()

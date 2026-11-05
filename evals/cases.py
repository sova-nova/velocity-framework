"""Eval cases: what the tutor sees before one learner message, and what its turn must do.

A case either points into a recorded session ("session" and "turn": the n-th learner
message) or is written by hand ("history" and "message"). Both become a `ReplayInput`.
Cases live in evals/cases/*.json; the recorded sessions they point into are in
evals/data/records/<subject>/.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from velocity.session import Event
from velocity.state import LearnerState

EVALS_DIR = Path(__file__).parent
CASES_DIR = EVALS_DIR / "cases"
DATA_DIR = EVALS_DIR / "data"


@dataclass(frozen=True)
class Turn:
    """One step of the history before the message under test."""

    role: str  # "learner", "tutor", "block" (added to the log with append_log), "annotation"
    text: str
    title: str | None = None  # blocks only


@dataclass(frozen=True)
class ReplayInput:
    subject_slug: str
    subject_name: str
    sources: tuple[str, ...]  # source names, e.g. ("Stewart",)
    date: date  # the day the original session took place
    history: tuple[Turn, ...]  # this session, before the message under test
    annotations: tuple[
        str, ...
    ]  # typed into the log just before the message under test
    message: str
    previous_sessions: tuple[
        str, ...
    ]  # recorded sessions before this one, oldest first
    notes: dict[str, str] = field(default_factory=dict)  # vault path -> markdown
    # This session's own state changes made before the message under test: the first N
    # questions and threads it recorded (the record doesn't say when they were made).
    session_id: str | None = None
    session_questions: int = 0
    session_threads: int = 0


@dataclass(frozen=True)
class Case:
    id: str
    about: str
    expect: dict
    raw: dict

    @property
    def judge(self) -> str:
        """What the LLM judge should pay attention to (used from PR 2 on)."""
        return self.raw.get("judge", "")


def load_cases(cases_dir: Path = CASES_DIR) -> list[Case]:
    cases = []
    for path in sorted(cases_dir.glob("*.json")):
        for raw in json.loads(path.read_text(encoding="utf-8")):
            cases.append(
                Case(raw["id"], raw.get("about", ""), raw.get("expect", {}), raw)
            )
    ids = [c.id for c in cases]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"duplicate case ids: {', '.join(dupes)}")
    return cases


def session_order(session_id: str) -> tuple[str, int]:
    day, n = session_id.rsplit("-", 1)
    return day, int(n)


def read_record(
    subject_slug: str, session_id: str, data_dir: Path = DATA_DIR
) -> list[Event]:
    path = data_dir / "records" / subject_slug / f"{session_id}.jsonl"
    return [Event(**json.loads(line)) for line in path.read_text().splitlines() if line]


def recorded_sessions(subject_slug: str, data_dir: Path = DATA_DIR) -> list[str]:
    paths = (data_dir / "records" / subject_slug).glob("*.jsonl")
    return sorted((p.stem for p in paths), key=session_order)


def replay_input(case: Case, data_dir: Path = DATA_DIR) -> ReplayInput:
    raw = case.raw
    if "session" in raw:
        return _from_record(raw, data_dir)
    return ReplayInput(
        subject_slug=raw["subject"].get("slug", "calculus"),
        subject_name=raw["subject"]["name"],
        sources=tuple(raw["subject"].get("sources", [])),
        date=date.fromisoformat(raw.get("date", "2026-11-05")),
        history=tuple(Turn(**t) for t in raw.get("history", [])),
        annotations=tuple(raw.get("annotations", [])),
        message=raw["message"],
        previous_sessions=tuple(raw.get("previous_sessions", [])),
        notes=raw["subject"].get("notes", {}),
    )


def _from_record(raw: dict, data_dir: Path) -> ReplayInput:
    slug = raw.get("subject_slug", "calculus")
    session_id, turn = raw["session"], raw["turn"]
    events = read_record(slug, session_id, data_dir)
    start = next(e for e in events if e.kind == "start")

    history: list[Turn] = []
    pending: list[str] = []  # annotations reported with the next learner message
    learner_count = 0
    message = None
    for e in events:
        if e.role == "event" and e.kind == "annotation":
            pending.append(e.text)
        elif e.role == "learner":
            learner_count += 1
            if learner_count == turn:
                message = e.text
                break
            history += [Turn("annotation", a) for a in pending]
            pending = []
            history.append(Turn("learner", e.text))
        elif e.role == "tutor":
            history.append(Turn("tutor", e.text))
        elif e.role == "block":
            history.append(Turn("block", e.text, e.title))
    if message is None:
        raise ValueError(
            f"{raw['id']}: session {session_id} has no learner turn {turn}"
        )

    earlier = [
        s
        for s in recorded_sessions(slug, data_dir)
        if session_order(s) < session_order(session_id)
    ]
    return ReplayInput(
        subject_slug=slug,
        subject_name=start.text,
        sources=tuple((start.meta or {}).get("sources", [])),
        date=date.fromisoformat(session_id.rsplit("-", 1)[0]),
        history=tuple(history),
        # A case can supply annotations the record lacks (logged before annotations were recorded).
        annotations=tuple(raw.get("annotations", pending)),
        message=message,
        previous_sessions=tuple(earlier),
        session_id=session_id,
        session_questions=raw.get("session_state", {}).get("questions", 0),
        session_threads=raw.get("session_state", {}).get("threads", 0),
    )


def state_before(
    previous_sessions: tuple[str, ...],
    session_date: date,
    today: date,
    state_json: str,
    session: tuple[str | None, int, int] = (None, 0, 0),
) -> LearnerState:
    """Learner state as it was after `previous_sessions`, rebuilt from the final state.

    Questions and threads from those sessions are replayed in order, plus the first N
    questions and threads of the current session (`session`: id, N questions, N threads).
    Topic status and source come from the final state, since their history isn't recorded.
    Dates are shifted so the session's own day becomes `today`, which keeps the same
    reviews due. Threads opened in the current session stay open.
    """
    state = LearnerState()
    session_id, n_questions, n_threads = session
    if not previous_sessions and not (n_questions or n_threads):
        return state
    final = LearnerState.from_json(state_json)
    current_questions = [q for q in final.questions if q.session == session_id]
    current_threads = [t for t in final.threads if t.session == session_id]
    if n_questions > len(current_questions) or n_threads > len(current_threads):
        raise ValueError(f"session {session_id} recorded fewer questions or threads")
    shift = today - session_date

    def day(iso: str) -> date:
        return date.fromisoformat(iso) + shift

    included = current_questions[:n_questions]
    for q in final.questions:
        if q.session in previous_sessions or q in included:
            topic = final.topics.get(q.topic)
            state.record_question(
                topic.name if topic else q.topic,
                q.question,
                q.outcome,
                q.session,
                day(q.date),
                q.note,
                topic.source if topic else None,
            )
    for topic_id, topic in state.topics.items():
        if topic_id in final.topics:
            topic.status = final.topics[topic_id].status
    for t in final.threads:
        if t in current_threads[:n_threads]:
            state.open_thread(t.text, t.session, day(t.opened))
        elif t.session in previous_sessions:
            opened = state.open_thread(t.text, t.session, day(t.opened))
            if t.closed and date.fromisoformat(t.closed) < session_date:
                state.close_thread(opened.id, day(t.closed))
    return state

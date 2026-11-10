"""Per-subject learner state: topics, a question log, open threads, and review scheduling.

Stored as <vault>/Tutor/<subject>/state.json, written only by velocity. A readable
progress.md is regenerated next to it after every change.

Scheduling: each topic has a review interval in days. A correct answer doubles it,
an answer that needed hints keeps it, and a wrong answer resets it to one day.
"""

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

Status = Literal["introduced", "practicing", "solid"]
Outcome = Literal["correct", "hinted", "incorrect"]

STATE_VERSION = 1


class StateError(Exception):
    pass


@dataclass
class Topic:
    name: str
    status: Status = "introduced"
    source: str | None = None  # e.g. "Stewart §2.4"
    first_seen: str = ""
    last_seen: str = ""
    interval_days: int = 1
    next_review: str | None = None


@dataclass
class Question:
    date: str
    session: str
    topic: str  # topic id
    question: str
    outcome: Outcome
    note: str = ""


@dataclass
class Thread:
    id: int
    text: str
    opened: str
    session: str
    closed: str | None = None


@dataclass
class LearnerState:
    topics: dict[str, Topic] = field(default_factory=dict)
    questions: list[Question] = field(default_factory=list)
    threads: list[Thread] = field(default_factory=list)

    # -- updates ---------------------------------------------------------------------

    def touch_topic(self, name: str, today: date, source: str | None = None) -> str:
        """Create the topic if new, mark it seen today, and return its id."""
        topic_id = topic_key(name)
        topic = self.topics.get(topic_id)
        if topic is None:
            topic = self.topics[topic_id] = Topic(
                name=name.strip(), first_seen=today.isoformat()
            )
        topic.last_seen = today.isoformat()
        if source:
            topic.source = source
        return topic_id

    def record_question(
        self,
        topic: str,
        question: str,
        outcome: Outcome,
        session: str,
        today: date,
        note: str = "",
        source: str | None = None,
    ) -> Topic:
        if outcome not in ("correct", "hinted", "incorrect"):
            raise StateError(
                f"outcome must be correct, hinted, or incorrect, not {outcome!r}"
            )
        topic_id = self.touch_topic(topic, today, source)
        t = self.topics[topic_id]
        if outcome == "correct":
            t.interval_days = t.interval_days * 2 if t.next_review else 1
        elif outcome == "incorrect":
            t.interval_days = 1
        t.next_review = (today + timedelta(days=t.interval_days)).isoformat()
        if t.status == "introduced":
            t.status = "practicing"
        self.questions.append(
            Question(today.isoformat(), session, topic_id, question, outcome, note)
        )
        return t

    def set_status(
        self, topic: str, status: Status, today: date, source: str | None = None
    ) -> Topic:
        if status not in ("introduced", "practicing", "solid"):
            raise StateError(
                f"status must be introduced, practicing, or solid, not {status!r}"
            )
        topic_id = self.touch_topic(topic, today, source)
        self.topics[topic_id].status = status
        return self.topics[topic_id]

    def open_thread(self, text: str, session: str, today: date) -> Thread:
        thread = Thread(
            max((t.id for t in self.threads), default=0) + 1,
            text.strip(),
            today.isoformat(),
            session,
        )
        self.threads.append(thread)
        return thread

    def close_thread(self, thread_id: int, today: date) -> Thread:
        thread = next((t for t in self.threads if t.id == thread_id), None)
        if thread is None:
            raise StateError(f"no thread #{thread_id}")
        thread.closed = today.isoformat()
        return thread

    # -- views -----------------------------------------------------------------------

    def due(self, today: date) -> list[Topic]:
        return sorted(
            (
                t
                for t in self.topics.values()
                if t.next_review and t.next_review <= today.isoformat()
            ),
            key=lambda t: t.next_review or "",
        )

    def weak(self, recent: int = 3) -> list[tuple[Topic, list[Question]]]:
        """Topics whose most recent answer was not fully correct, with those recent answers."""
        result = []
        for topic_id, topic in self.topics.items():
            answers = [q for q in self.questions if q.topic == topic_id][-recent:]
            if answers and answers[-1].outcome != "correct":
                result.append((topic, answers))
        return result

    def open_threads(self) -> list[Thread]:
        return [t for t in self.threads if t.closed is None]

    def summary(self, today: date, limit: int = 8) -> str:
        """Compact summary for the tutor at session start."""
        if not self.topics and not self.threads:
            return "Learner state: nothing recorded yet for this subject."
        lines = ["Learner state:"]
        due = self.due(today)
        lines.append(
            "Due for review: "
            + (", ".join(f"{t.name} ({t.status})" for t in due[:limit]) or "nothing")
        )
        weak = self.weak()
        if weak:
            lines.append("Weak spots (last answer not correct):")
            for topic, answers in weak[:limit]:
                last = answers[-1]
                note = f" ({last.note})" if last.note else ""
                lines.append(
                    f'- {topic.name}: {last.outcome} on "{last.question}"{note}'
                )
        threads = self.open_threads()
        if threads:
            lines.append("Open threads:")
            lines += [f"- #{t.id} {t.text} (since {t.opened})" for t in threads[:limit]]
        recent = sorted(self.topics.values(), key=lambda t: t.last_seen, reverse=True)[
            :limit
        ]
        lines.append(
            "Recently studied: "
            + ", ".join(f"{t.name} ({t.status}, last {t.last_seen})" for t in recent)
        )
        return "\n".join(lines)

    def progress_view(self, today: date, recent: int = 10) -> dict:
        """What the web page's Progress tab shows (decided 2026-11-10): the same lists the
        tutor sees at session start, then every topic with its questions, then the latest
        questions. `days` is days until the next review (negative when overdue)."""

        def days(topic: Topic) -> int | None:
            if not topic.next_review:
                return None
            return (date.fromisoformat(topic.next_review) - today).days

        def question(q: Question) -> dict:
            name = self.topics[q.topic].name if q.topic in self.topics else q.topic
            return {**asdict(q), "topic_name": name}

        by_topic: dict[str, list[dict]] = {}
        for q in self.questions:
            by_topic.setdefault(q.topic, []).append(question(q))
        ordered = sorted(
            self.topics.items(),
            key=lambda kv: (kv[1].next_review or "9999", kv[1].name),
        )
        return {
            "due": [
                {"name": t.name, "days": days(t), "source": t.source}
                for t in self.due(today)
            ],
            "weak": [
                {"name": t.name, "last": question(answers[-1])}
                for t, answers in self.weak()
            ],
            "threads": [asdict(t) for t in self.open_threads()],
            "topics": [
                {
                    **asdict(t),
                    "id": topic_id,
                    "days": days(t),
                    "questions": by_topic.get(topic_id, [])[::-1],
                }
                for topic_id, t in ordered
            ],
            "recent": [question(q) for q in self.questions[-recent:][::-1]],
        }

    def progress_markdown(self, subject_name: str, today: date) -> str:
        lines = [
            "<!-- Generated by velocity from state.json after every change. Edits are overwritten;",
            "     to correct something, tell the tutor. -->",
            f"# {subject_name}: progress",
            "",
            f"*Updated {today.isoformat()}*",
            "",
            "## Topics",
            "",
        ]
        if self.topics:
            lines += [
                "| Topic | Status | Source | Last seen | Next review |",
                "|---|---|---|---|---|",
            ]
            for t in sorted(
                self.topics.values(), key=lambda t: (t.next_review or "9999", t.name)
            ):
                lines.append(
                    f"| {t.name} | {t.status} | {t.source or ''} | {t.last_seen} | {t.next_review or ''} |"
                )
        else:
            lines.append("Nothing yet.")
        lines += ["", "## Open threads", ""]
        lines += [
            f"- #{t.id} {t.text} *(since {t.opened})*" for t in self.open_threads()
        ] or ["None."]
        lines += ["", "## Recent questions", ""]
        recent = self.questions[-15:][::-1]
        if recent:
            lines += ["| Date | Topic | Question | Outcome |", "|---|---|---|---|"]
            for q in recent:
                name = self.topics[q.topic].name if q.topic in self.topics else q.topic
                question = q.question.replace("|", "\\|")
                lines.append(f"| {q.date} | {name} | {question} | {q.outcome} |")
        else:
            lines.append("None yet.")
        return "\n".join(lines) + "\n"

    # -- persistence -----------------------------------------------------------------

    def to_json(self) -> str:
        data = {
            "version": STATE_VERSION,
            "topics": {k: asdict(v) for k, v in self.topics.items()},
            "questions": [asdict(q) for q in self.questions],
            "threads": [asdict(t) for t in self.threads],
        }
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "LearnerState":
        try:
            data = json.loads(text)
            return cls(
                topics={k: Topic(**v) for k, v in data.get("topics", {}).items()},
                questions=[Question(**q) for q in data.get("questions", [])],
                threads=[Thread(**t) for t in data.get("threads", [])],
            )
        except (json.JSONDecodeError, TypeError, AttributeError) as e:
            raise StateError(f"state.json is not valid learner state: {e}") from None


def topic_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not key:
        raise StateError(f"topic name {name!r} has no usable characters")
    return key


def load_state(subject_dir: Path) -> LearnerState:
    path = subject_dir / "state.json"
    if not path.exists():
        return LearnerState()
    return LearnerState.from_json(path.read_text(encoding="utf-8"))


def save_state(
    state: LearnerState, subject_dir: Path, subject_name: str, today: date
) -> None:
    """Write state.json atomically and regenerate progress.md."""
    subject_dir.mkdir(parents=True, exist_ok=True)
    tmp = subject_dir / "state.json.tmp"
    tmp.write_text(state.to_json(), encoding="utf-8")
    os.replace(tmp, subject_dir / "state.json")
    (subject_dir / "progress.md").write_text(
        state.progress_markdown(subject_name, today), encoding="utf-8"
    )

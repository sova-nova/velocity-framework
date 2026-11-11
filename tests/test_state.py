from datetime import date, timedelta

import pytest

from velocity.state import LearnerState, StateError, load_state, save_state

D0 = date(2026, 11, 4)


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def answer(
    state: LearnerState,
    outcome: str,
    when: date = D0,
    topic: str = "Epsilon-delta definition",
):
    return state.record_question(
        topic, "Prove lim (4x-5) = 7", outcome, "2026-11-04-1", when
    )


def test_first_answer_creates_topic_and_schedules_tomorrow():
    s = LearnerState()
    t = answer(s, "correct")
    assert list(s.topics) == ["epsilon-delta-definition"]
    assert (t.status, t.first_seen, t.next_review) == (
        "practicing",
        "2026-11-04",
        "2026-11-05",
    )


def test_correct_doubles_hinted_keeps_incorrect_resets():
    s = LearnerState()
    assert answer(s, "correct", day(0)).interval_days == 1
    assert answer(s, "correct", day(1)).interval_days == 2
    assert answer(s, "correct", day(3)).interval_days == 4
    t = answer(s, "hinted", day(7))
    assert (t.interval_days, t.next_review) == (4, day(11).isoformat())
    t = answer(s, "incorrect", day(11))
    assert (t.interval_days, t.next_review) == (1, day(12).isoformat())


def test_partly_right_keeps_the_interval_and_counts_as_a_weak_spot():
    s = LearnerState()
    answer(s, "correct", day(0))
    answer(s, "correct", day(1))
    t = answer(s, "partial", day(3))
    assert (t.interval_days, t.next_review) == (2, day(5).isoformat())
    assert "Weak spots" in s.summary(day(3))


def test_topic_names_are_matched_loosely():
    s = LearnerState()
    answer(s, "correct", topic="Epsilon-Delta definition")
    answer(s, "correct", topic="epsilon delta  definition")
    assert len(s.topics) == 1
    assert s.topics["epsilon-delta-definition"].name == "Epsilon-Delta definition"


def test_invalid_values_are_rejected():
    s = LearnerState()
    with pytest.raises(StateError, match="outcome"):
        answer(s, "great")
    with pytest.raises(StateError, match="status"):
        s.set_status("limits", "mastered", D0)
    with pytest.raises(StateError, match="no thread"):
        s.close_thread(3, D0)


def test_threads():
    s = LearnerState()
    s.open_thread("Why does delta depend on epsilon?", "s1", D0)
    second = s.open_thread("Revisit one-sided limits", "s1", D0)
    s.close_thread(1, day(1))
    assert [t.id for t in s.open_threads()] == [second.id]


def test_summary_for_session_start():
    s = LearnerState()
    answer(s, "correct", topic="Limits")
    answer(s, "incorrect", topic="Epsilon-delta definition")
    s.open_thread("Why does delta depend on epsilon?", "s1", D0)
    text = s.summary(day(1))
    assert (
        "Due for review: Limits (practicing), Epsilon-delta definition (practicing)"
        in text
    )
    assert 'Epsilon-delta definition: incorrect on "Prove lim (4x-5) = 7"' in text
    assert "#1 Why does delta depend on epsilon?" in text
    assert (
        LearnerState().summary(D0)
        == "Learner state: nothing recorded yet for this subject."
    )


def test_save_load_roundtrip_and_progress_page(tmp_path):
    s = LearnerState()
    answer(s, "hinted")
    s.set_status("Limits", "solid", D0, source="Stewart §2.2")
    s.open_thread("a | pipe", "s1", D0)
    save_state(s, tmp_path, "Calculus", D0)

    loaded = load_state(tmp_path)
    assert loaded.to_json() == s.to_json()
    assert not (tmp_path / "state.json.tmp").exists()
    progress = (tmp_path / "progress.md").read_text()
    assert "# Calculus: progress" in progress
    assert "| Limits | solid | Stewart §2.2 | 2026-11-04 |  |" in progress
    assert "- #1 a | pipe" in progress
    assert (
        "| 2026-11-04 | Epsilon-delta definition | Prove lim (4x-5) = 7 | hinted |"
        in progress
    )


def test_missing_state_is_empty_and_corrupt_state_errors(tmp_path):
    assert load_state(tmp_path).topics == {}
    (tmp_path / "state.json").write_text("{not json")
    with pytest.raises(StateError, match="not valid"):
        load_state(tmp_path)


def test_progress_view_for_the_web_page():
    s = LearnerState()
    s.record_question("Limits", "lim x->2 of x^2", "correct", "2026-11-04-1", D0)
    s.record_question("Limits", "Explain a limit", "hinted", "2026-11-04-1", D0)
    s.record_question("Derivatives", "What is f'(a)?", "correct", "2026-11-04-1", D0)
    s.record_question("Derivatives", "d/dx x^2", "correct", "2026-11-04-1", day(1))
    s.open_thread("Return to the derivative", "2026-11-04-1", D0)
    closed = s.open_thread("Done already", "2026-11-04-1", D0)
    s.close_thread(closed.id, D0)

    view = s.progress_view(day(2), recent=3)
    # Limits was due on day 1 (one day overdue); Derivatives (interval 2) on day 3.
    assert view["due"] == [{"name": "Limits", "days": -1, "source": None}]
    assert [(w["name"], w["last"]["question"]) for w in view["weak"]] == [
        ("Limits", "Explain a limit")
    ]
    assert [t["text"] for t in view["threads"]] == ["Return to the derivative"]
    # Topics by next review; each with its questions, newest first.
    assert [(t["name"], t["days"]) for t in view["topics"]] == [
        ("Limits", -1),
        ("Derivatives", 1),
    ]
    assert [q["question"] for q in view["topics"][0]["questions"]] == [
        "Explain a limit",
        "lim x->2 of x^2",
    ]
    assert [(q["question"], q["topic_name"]) for q in view["recent"]] == [
        ("d/dx x^2", "Derivatives"),
        ("What is f'(a)?", "Derivatives"),
        ("Explain a limit", "Limits"),
    ]


def test_progress_view_when_empty():
    assert LearnerState().progress_view(D0) == {
        "due": [],
        "weak": [],
        "threads": [],
        "topics": [],
        "recent": [],
    }

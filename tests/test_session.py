from datetime import UTC, datetime, timedelta

import pytest

from velocity.session import ENDED_WITH_WRAP_UP, LogCheck, Session

T0 = datetime(2026, 11, 4, 18, 2, tzinfo=UTC)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture
def dirs(tmp_path):
    return tmp_path / "records", tmp_path / "vault/Tutor/calculus/sessions"


def start(dirs, sources=("Stewart",), now=T0) -> Session:
    return Session.start("calculus", "Calculus", *dirs, sources=list(sources), now=now)


def test_start_writes_header_and_numbers_sessions_per_day(dirs):
    s1 = start(dirs)
    assert s1.id == "2026-11-04-1"
    assert s1.log_path.read_text() == (
        "---\nsubject: calculus\nsession: 2026-11-04-1\nstarted: 2026-11-04T18:02\n"
        "sources: [Stewart]\n---\n# Calculus — 2026-11-04 (1)\n\n"
    )
    assert start(dirs).id == "2026-11-04-2"
    assert start(dirs, now=T0 + timedelta(days=1)).id == "2026-11-05-1"


def test_turns_and_blocks_render_in_timestamp_order(dirs):
    s = start(dirs)
    s.append("learner", "Explain epsilon-delta?", now=at(1))
    s.render()
    # Within a turn the block is recorded before the tutor text arrives (at turn end),
    # but it happened after the first part of the reply.
    s.append(
        "block",
        "Prove that $\\lim_{x \\to 3}(4x-5) = 7$.",
        title="Problem 1",
        kind="example",
        now=at(5),
    )
    s.append("tutor", "Here is a problem to try:", now=at(4))
    s.append("tutor", "Try it, then tell me your delta.", now=at(6))
    s.render()
    body = s.log_path.read_text().split("(1)\n\n", 1)[1]
    assert body == (
        "**You:** Explain epsilon-delta?\n\n"
        "**Tutor:** Here is a problem to try:\n\n"
        "> [!example] Problem 1\n> Prove that $\\lim_{x \\to 3}(4x-5) = 7$.\n\n"
        "**Tutor:** Try it, then tell me your delta.\n\n"
    )


def test_render_only_appends_new_events(dirs):
    s = start(dirs)
    s.append("learner", "hi", now=at(1))
    s.render()
    s.render()
    assert s.log_path.read_text().count("**You:** hi") == 1


def test_end_footer_and_no_appends_after_end(dirs):
    s = start(dirs)
    s.end(ENDED_WITH_WRAP_UP, now=at(9))
    assert s.log_path.read_text().endswith("---\n*Session ended with /end-session.*\n")
    s.end(now=at(10))  # idempotent
    assert s.log_path.read_text().count("Session ended") == 1
    with pytest.raises(RuntimeError, match="ended"):
        s.append("learner", "too late")


def test_quit_without_wrap_up(dirs):
    s = start(dirs)
    s.end()
    assert s.log_path.read_text().endswith("*Session ended without wrap-up.*\n")


def test_record_is_source_of_truth_and_survives_a_new_instance(dirs):
    s = start(dirs)
    s.append("learner", "hi", now=at(1))
    again = Session("calculus", s.id, *dirs)
    assert [e.role for e in again.events()] == ["event", "learner"]
    assert again.events()[0].meta == {"sources": ["Stewart"]}


def make_dialogue(dirs) -> Session:
    s = start(dirs)
    s.append("learner", "What is a limit?", now=at(1))
    s.append("tutor", "What do you think it is?", now=at(2))
    s.append("learner", "Where f goes near a.", now=at(3))
    s.append("tutor", "Good. Now make that precise.", now=at(4))
    s.render()
    return s


def test_untouched_log_checks_clean(dirs):
    assert make_dialogue(dirs).check_log() == LogCheck([], [])


def test_learner_annotations_are_detected(dirs):
    s = make_dialogue(dirs)
    text = s.log_path.read_text().replace(
        "**You:** Where f goes", "> I wasn't sure here.\n\n**You:** Where f goes"
    )
    s.log_path.write_text(text + "\nMy own summary.\n")
    check = s.check_log()
    assert check.missing == []
    assert check.annotations == ["> I wasn't sure here.", "My own summary."]


def test_deleted_text_is_detected_and_restorable(dirs):
    s = make_dialogue(dirs)
    s.log_path.write_text(
        s.log_path.read_text().replace("**Tutor:** What do you think it is?\n\n", "")
    )
    assert s.check_log().missing == ["**Tutor:** What do you think it is?"]
    s.restore_log()
    assert s.check_log() == LogCheck([], [])
    assert "sources: [Stewart]" in s.log_path.read_text()


def test_edited_properties_block_is_not_flagged(dirs):
    s = make_dialogue(dirs)
    s.log_path.write_text(
        s.log_path.read_text().replace(
            "sources: [Stewart]", "sources:\n  - Stewart\ntags: [math]"
        )
    )
    assert s.check_log() == LogCheck([], [])

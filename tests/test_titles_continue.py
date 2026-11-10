"""Session titles and continuing a finished session (core, all front ends)."""

import io
import json
from datetime import UTC, datetime, timedelta

import pytest

from velocity import cli, hooks
from velocity.config import Config, ConfigError, create_subject, load_subject
from velocity.session import (
    ENDED_WITH_WRAP_UP,
    RECAP_TITLE,
    Session,
    active_session_id,
    record_dir,
    session_summaries,
    set_active,
    with_title_property,
)

T0 = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv(hooks.CONTINUE_ENV, raising=False)


@pytest.fixture
def config(tmp_path) -> Config:
    (tmp_path / "vault").mkdir()
    config = Config((tmp_path / "vault").resolve())
    create_subject(config, "Calculus")
    return config


@pytest.fixture
def subject(config):
    return load_subject(config, "calculus")


def finished(subject, how=ENDED_WITH_WRAP_UP, title=None, day=0) -> Session:
    now = T0 + timedelta(days=day)
    s = Session.start(
        "calculus",
        "Calculus",
        record_dir("calculus"),
        subject.sessions_dir,
        sources=[],
        now=now,
    )
    s.append("learner", "Teach me limits", now=now + timedelta(seconds=1))
    s.append("tutor", "What do you know?", now=now + timedelta(seconds=2))
    if title:
        s.set_title(title, now=now + timedelta(seconds=3))
    if how == ENDED_WITH_WRAP_UP:
        s.append(
            "block",
            "Covered limits.",
            title=RECAP_TITLE,
            kind="summary",
            now=now + timedelta(seconds=4),
        )
    s.render()
    s.end(how, now=now + timedelta(minutes=5))
    return s


# -- titles -----------------------------------------------------------------------------


def test_title_is_recorded_and_shown_as_a_property(subject):
    s = finished(subject, how="no-wrap-up", title="Limits: the idea")
    text = s.log_path.read_text()
    assert text.startswith("---\nsubject: calculus\n")
    assert 'title: "Limits: the idea"\n---\n# Calculus' in text
    assert s.title == "Limits: the idea"

    # A new title replaces the property; the dialogue is untouched.
    s.reopen()
    s.set_title('One-sided limits, "DNE"')
    text = s.log_path.read_text()
    assert text.count("title:") == 1
    assert 'title: "One-sided limits, \\"DNE\\""' in text
    assert "**You:** Teach me limits" in text
    assert s.check_log().missing == [] and s.check_log().annotations == []
    assert 'title: "One-sided' in s.rendered_text()


def test_title_validation(subject):
    s = finished(subject, how="no-wrap-up")
    s.reopen()
    with pytest.raises(ValueError, match="empty"):
        s.set_title("  \n ")
    with pytest.raises(ValueError, match="longer than"):
        s.set_title("x" * 101)


def test_title_property_needs_a_properties_block():
    assert with_title_property("# No properties\n", "T") == "# No properties\n"
    assert with_title_property("---\na: 1\n---\nbody", "T") == (
        '---\na: 1\ntitle: "T"\n---\nbody'
    )


def test_summaries_include_titles_and_links(subject):
    first = finished(subject, title="Limits: the idea")
    second = Session.start(
        "calculus",
        "Calculus",
        record_dir("calculus"),
        subject.sessions_dir,
        sources=[],
        now=T0 + timedelta(days=1),
        continues=first.id,
    )
    set_active("calculus", second.id)
    summaries = session_summaries("calculus", subject.sessions_dir)
    assert [(s.id, s.title, s.ended, s.continues) for s in summaries] == [
        (second.id, None, None, first.id),
        (first.id, "Limits: the idea", "wrapped-up", None),
    ]
    assert f'continues: "[[{first.id}]]"' in second.log_path.read_text()


# -- continuing a session ---------------------------------------------------------------


def start_hook(config, subject, **payload):
    return hooks.session_start(config, subject, {"session_id": "c9", **payload})


def test_session_start_continues_a_finished_session(config, subject):
    first = finished(subject, title="Limits: the idea")
    ctx = start_hook(config, subject, continues=first.id)["hookSpecificOutput"]
    text = ctx["additionalContext"]
    new = Session(
        "calculus",
        active_session_id("calculus"),
        record_dir("calculus"),
        subject.sessions_dir,
    )
    assert new.id != first.id and new.continues == first.id
    assert f"continues session {first.id} (Limits: the idea)" in text
    assert "read it with read_session" in text and "Covered limits." in text
    assert "no title yet" in text


def test_continue_from_the_environment(config, subject, monkeypatch):
    first = finished(subject)
    monkeypatch.setenv(hooks.CONTINUE_ENV, first.id)
    start_hook(config, subject)
    new = Session(
        "calculus",
        active_session_id("calculus"),
        record_dir("calculus"),
        subject.sessions_dir,
    )
    assert new.continues == first.id


@pytest.mark.parametrize("bad", ["2026-01-01-1", "../x", ""])
def test_unknown_sessions_are_not_continued(config, subject, bad):
    finished(subject)
    start_hook(config, subject, continues=bad)
    new = Session(
        "calculus",
        active_session_id("calculus"),
        record_dir("calculus"),
        subject.sessions_dir,
    )
    assert new.continues is None


def test_resumed_session_context_shows_its_title(config, subject):
    claude_map = record_dir("calculus") / "claude-sessions"
    start_hook(config, subject)
    session_id = active_session_id("calculus")
    claude_map.joinpath("c9").write_text(session_id)
    s = Session("calculus", session_id, record_dir("calculus"), subject.sessions_dir)
    s.set_title("Derivatives")
    hooks.session_end(config, subject, {"reason": "prompt_input_exit"})
    text = start_hook(config, subject, source="resume")["hookSpecificOutput"][
        "additionalContext"
    ]
    assert "(resumed)" in text and "Session title so far: Derivatives" in text


# -- commands ---------------------------------------------------------------------------


@pytest.fixture
def use_config(config, monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda: config)
    return config


def test_cli_sessions(use_config, subject, capsys):
    assert cli.main(["sessions", "calculus"]) == 0
    assert "No sessions yet." in capsys.readouterr().out
    first = finished(subject, title="Limits: the idea")
    finished(subject, how="no-wrap-up", day=1)
    assert cli.main(["sessions", "calculus"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("2026-11-05-1  (untitled) Teach me limits  [paused")
    assert lines[1] == f"{first.id}  Limits: the idea  [wrapped up, 1 messages]"


def test_cli_tutor_continue(use_config, subject, monkeypatch):
    first = finished(subject)
    launched = {}
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(cli.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        cli.os, "execv", lambda path, args: launched.update(path=path, args=args)
    )
    assert cli.main(["tutor", "calculus", "--continue", first.id]) == 0
    assert cli.os.environ[hooks.CONTINUE_ENV] == first.id
    assert launched["path"] == "/bin/claude"


@pytest.mark.parametrize(
    "args, message",
    [
        (["--continue", "2026-01-01-1"], "no session"),
        (["--continue", "OPEN"], "still open"),
        (["--resume", "--continue", "DONE"], "either --resume or --continue"),
    ],
)
def test_cli_tutor_continue_errors(use_config, subject, monkeypatch, args, message):
    done = finished(subject)
    open_ = Session.start(
        "calculus", "Calculus", record_dir("calculus"), subject.sessions_dir, []
    )
    args = [{"DONE": done.id, "OPEN": open_.id}.get(a, a) for a in args]
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/claude")
    with pytest.raises(ConfigError, match=message):
        cli._tutor("calculus", "--resume" in args, args[-1])


def test_cli_chat_continue_passes_the_session(use_config, subject, monkeypatch):
    first = finished(subject)
    seen = {}

    async def fake_chat(config, subject, out, read_line, model=None, continues=None):
        seen["continues"] = continues

    monkeypatch.setattr("velocity.agent.chat", fake_chat)
    assert cli.main(["chat", "calculus", "--continue", first.id]) == 0
    assert seen["continues"] == first.id


def test_agent_start_passes_continues_to_the_hook(config, subject):
    from velocity.agent import TutorAgent

    first = finished(subject)
    agent = TutorAgent(
        config=config,
        subject=subject,
        stream=None,
        call_tool=None,
        tools=[],
        skills={},
        out=io.StringIO(),
        continues=first.id,
    )
    agent.start()
    record = record_dir("calculus") / f"{active_session_id('calculus')}.jsonl"
    start = json.loads(record.read_text().splitlines()[0])
    assert start["meta"]["continues"] == first.id


# -- cut-off sessions (decided 2026-11-10) ----------------------------------------------


def cut_off(subject) -> Session:
    """A session whose process stopped without an end event, no longer active."""
    s = Session.start(
        "calculus", "Calculus", record_dir("calculus"), subject.sessions_dir, sources=[]
    )
    s.append("learner", "Teach me limits")
    return s


def status(subject) -> dict:
    return {s.id: s.ended for s in session_summaries("calculus", subject.sessions_dir)}


def test_a_session_is_open_only_while_active(subject):
    from velocity.session import set_active

    s = cut_off(subject)
    set_active("calculus", s.id)
    assert status(subject) == {s.id: None}
    set_active("calculus", None)
    assert status(subject) == {s.id: "no-wrap-up"}


def test_a_cut_off_session_can_be_resumed(config, subject):
    s = cut_off(subject)
    start_hook(config, subject, reopen=s.id)
    assert active_session_id("calculus") == s.id
    assert status(subject) == {s.id: None}


def test_the_active_session_is_not_taken_over_by_resume(config, subject):
    from velocity.session import set_active

    s = cut_off(subject)
    set_active("calculus", s.id)
    start_hook(config, subject, reopen=s.id)
    new = active_session_id("calculus")
    assert new != s.id
    # Starting the new session ended the one it took over from, as paused.
    assert status(subject) == {new: None, s.id: "no-wrap-up"}
    assert Session("calculus", s.id, record_dir("calculus"), subject.sessions_dir).ended


def test_starting_a_session_pauses_the_one_it_takes_over_from(config, subject):
    start_hook(config, subject)
    first = active_session_id("calculus")
    hooks.user_prompt(config, subject, {"prompt": "Teach me limits"})
    start_hook(config, subject, session_id="other")
    second = active_session_id("calculus")
    assert second != first
    events = Session(
        "calculus", first, record_dir("calculus"), subject.sessions_dir
    ).events()
    assert [e.text for e in events if e.kind == "end"] == ["no-wrap-up"]
    assert status(subject) == {second: None, first: "no-wrap-up"}


# -- empty sessions (decided 2026-11-10) ------------------------------------------------


def session_files(s: Session) -> list[bool]:
    return [s.record_path.exists(), s.log_path.exists()]


def test_an_empty_session_is_removed_when_it_ends(config, subject):
    claude_map = record_dir("calculus") / "claude-sessions"
    start_hook(config, subject)
    s = Session(
        "calculus",
        active_session_id("calculus"),
        record_dir("calculus"),
        subject.sessions_dir,
    )
    assert claude_map.joinpath("c9").read_text() == s.id
    hooks.session_end(config, subject, {"reason": "prompt_input_exit"})
    assert session_files(s) == [False, False]
    assert not claude_map.joinpath("c9").exists()
    assert active_session_id("calculus") is None
    assert status(subject) == {}


def test_a_session_with_anything_in_it_is_kept(config, subject):
    start_hook(config, subject)
    s = Session(
        "calculus",
        active_session_id("calculus"),
        record_dir("calculus"),
        subject.sessions_dir,
    )
    s.set_title("Limits")  # a title alone is enough
    hooks.session_end(config, subject, {"reason": "prompt_input_exit"})
    assert session_files(s) == [True, True] and s.ended


def test_an_empty_session_with_the_learners_writing_is_kept(config, subject):
    start_hook(config, subject)
    s = Session(
        "calculus",
        active_session_id("calculus"),
        record_dir("calculus"),
        subject.sessions_dir,
    )
    with s.log_path.open("a") as f:
        f.write("\nMy own thought before starting.\n")
    hooks.session_end(config, subject, {"reason": "prompt_input_exit"})
    assert session_files(s) == [True, True] and s.ended


def test_an_empty_session_taken_over_is_removed(config, subject):
    start_hook(config, subject)
    first = Session(
        "calculus",
        active_session_id("calculus"),
        record_dir("calculus"),
        subject.sessions_dir,
    )
    start_hook(config, subject, session_id="other")
    assert session_files(first) == [False, False]
    assert list(status(subject).values()) == [None]


def test_end_session_first_never_reaches_the_tutor(config, subject):
    start_hook(config, subject)
    blocked = hooks.user_prompt(config, subject, {"prompt": "/end-session"})
    assert blocked["decision"] == "block" and "/exit" in blocked["reason"]
    # Not logged; the session stays empty, so quitting removes it.
    session_id = active_session_id("calculus")
    s = Session("calculus", session_id, record_dir("calculus"), subject.sessions_dir)
    assert s.empty
    # Once something happened, /end-session goes to the tutor as usual.
    hooks.user_prompt(config, subject, {"prompt": "What is a limit?"})
    assert hooks.user_prompt(config, subject, {"prompt": "/end-session"}) == {}

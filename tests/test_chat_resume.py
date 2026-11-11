"""`velocity chat <subject> --resume [SESSION]` (decided 2026-11-10)."""

from datetime import UTC, datetime, timedelta

import pytest

from velocity import cli
from velocity.agent import RESUME_COST_NOTE, resume_recap
from velocity.config import Config, create_subject, load_subject
from velocity.session import (
    ENDED_WITH_WRAP_UP,
    Event,
    Session,
    record_dir,
    set_active,
)

T0 = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.fixture
def config(tmp_path, monkeypatch) -> Config:
    (tmp_path / "vault").mkdir()
    config = Config((tmp_path / "vault").resolve())
    create_subject(config, "Calculus")
    monkeypatch.setattr(cli, "load_config", lambda: config)
    return config


@pytest.fixture
def subject(config):
    return load_subject(config, "calculus")


def session(subject, day, messages=4, how="no-wrap-up", title=None, model=None):
    now = T0 + timedelta(days=day)
    s = Session.start(
        "calculus",
        "Calculus",
        record_dir("calculus"),
        subject.sessions_dir,
        sources=[],
        now=now,
    )
    for i in range(messages):
        role = "learner" if i % 2 == 0 else "tutor"
        s.append(role, f"{role} message {i + 1}", now=now + timedelta(seconds=i + 1))
    if model:
        s.append(
            "event",
            "",
            kind="usage",
            meta={"model": model},
            now=now + timedelta(seconds=30),
        )
    if title:
        s.set_title(title, now=now + timedelta(seconds=40))
    s.render()
    if how:
        s.end(how, now=now + timedelta(minutes=5))
    return s


@pytest.fixture
def started(monkeypatch):
    """Runs `velocity chat` without the API: what it would have started."""
    seen = {}

    async def fake_chat(config, subject, out, read_line, model=None, **options):
        seen.update(model=model, **options)

    monkeypatch.setattr("velocity.agent.chat", fake_chat)
    return seen


def test_resume_picks_the_newest_paused_session(subject, started):
    older = session(subject, 0)
    newer = session(subject, 1)
    session(subject, 2, how=ENDED_WITH_WRAP_UP)  # finished: continued, not resumed
    assert cli.main(["chat", "calculus", "--resume"]) == 0
    assert started["reopen"] == newer.id and started["continues"] is None
    assert cli.main(["chat", "calculus", "--resume", older.id]) == 0
    assert started["reopen"] == older.id


@pytest.mark.parametrize(
    "case, message",
    [
        ("none paused", "no paused sessions in Calculus"),
        ("unknown", "no session '2026-01-01-1' in calculus"),
        (
            "wrapped up",
            "was wrapped up; continue it in a new session with: velocity chat calculus --continue",
        ),
        ("open", "is open somewhere else; end or pause it there first"),
        ("both", "use either --resume or --continue, not both"),
    ],
)
def test_resume_refuses_with_what_to_do(subject, started, capsys, case, message):
    done = session(subject, 0, how=ENDED_WITH_WRAP_UP)
    args = ["chat", "calculus", "--resume"]
    if case == "unknown":
        args.append("2026-01-01-1")
    elif case == "wrapped up":
        args.append(done.id)
    elif case == "open":
        open_now = session(subject, 1, how=None)
        set_active("calculus", open_now.id)
        args.append(open_now.id)
    elif case == "both":
        args += ["--continue", done.id]
    assert cli.main(args) == 1
    assert message in capsys.readouterr().err
    assert not started  # nothing started


def test_resume_defaults_to_the_model_that_ran_it_last(subject, started):
    by_sonnet = session(subject, 0, model="claude-sonnet-5-5")
    assert cli.main(["chat", "calculus", "--resume", by_sonnet.id]) == 0
    assert started["model"] == "claude-sonnet-5-5"
    assert (
        cli.main(["chat", "calculus", "--resume", by_sonnet.id, "--model", "haiku"])
        == 0
    )
    assert started["model"] == "claude-haiku-5-5"  # --model wins


def test_resume_after_claude_code_uses_the_configured_model(subject, started):
    s = session(subject, 0)
    s._write_record(  # a web chat on Claude Code ran it last
        Event(
            (T0 + timedelta(minutes=1)).isoformat(),
            "event",
            "",
            kind="engine",
            meta={"engine": "claude-code", "model": "opus"},
        )
    )
    assert cli.main(["chat", "calculus", "--resume"]) == 0
    assert (
        started["model"] == "claude-opus-5-5"
    )  # velocity's default, not Claude Code's


def test_recap_shows_the_last_three_messages_in_full(subject):
    long_reply = "A long reply.\n\n" + "More of it. " * 50
    s = session(subject, 0, messages=4, title="Limits: $0/0$ forms")
    s = Session("calculus", s.id, record_dir("calculus"), subject.sessions_dir)
    recap = resume_recap(s)
    assert recap.startswith(f'Resuming "Limits: $0/0$ forms" ({s.id}).\n')
    assert "Where you left off (the last 3 messages):" in recap
    assert "learner message 1" not in recap
    assert (
        "\ntutor> tutor message 2\n" in recap and "\nyou> learner message 3\n" in recap
    )
    assert recap.rstrip().endswith(RESUME_COST_NOTE)

    short = session(subject, 1, messages=1)
    short = Session("calculus", short.id, record_dir("calculus"), subject.sessions_dir)
    short.reopen()
    short.append("tutor", long_reply)
    recap = resume_recap(short)
    assert recap.startswith(f"Resuming {short.id}.\n")
    assert "(the last 2 messages)" in recap and long_reply.strip() in recap

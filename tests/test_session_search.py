"""Searching sessions: History's search box and `velocity sessions --search` (2026-11-10)."""

from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from velocity import cli
from velocity.config import Config, create_subject, load_subject
from velocity.session import Session, record_dir, search_sessions
from velocity.web import build_app

T0 = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.fixture
def config(tmp_path) -> Config:
    (tmp_path / "vault").mkdir()
    config = Config((tmp_path / "vault").resolve())
    create_subject(config, "Calculus")
    return config


@pytest.fixture
def subject(config):
    return load_subject(config, "calculus")


def session(subject, day, learner, tutor, title=None, block=None) -> Session:
    now = T0 + timedelta(days=day)
    s = Session.start(
        "calculus",
        "Calculus",
        record_dir("calculus"),
        subject.sessions_dir,
        sources=[],
        now=now,
    )
    s.append("learner", learner, now=now + timedelta(seconds=1))
    s.append("tutor", tutor, now=now + timedelta(seconds=2))
    if block:
        s.append(
            "block",
            block,
            title="Example",
            kind="example",
            now=now + timedelta(seconds=3),
        )
    if title:
        s.set_title(title, now=now + timedelta(seconds=4))
    s.render()
    s.end(now=now + timedelta(minutes=5))
    return s


def found(subject, query):
    return [
        (m.session.id, m.in_title, m.snippet)
        for m in search_sessions("calculus", subject.sessions_dir, query)
    ]


def test_every_word_in_any_order_and_case(subject):
    s = session(subject, 0, "Teach me the Chain Rule", "Here is an example: $f(g(x))$.")
    assert found(subject, "example chain")[0][0] == s.id
    assert found(subject, "CHAIN rule") == [(s.id, True, None)]  # in its name
    assert found(subject, "chain product") == []  # every word must be there
    assert found(subject, "   ") == []


def test_name_matches_come_first_then_text_matches_newest_first(subject):
    older_text = session(subject, 0, "Hello", "Today: limits at infinity.")
    by_name = session(subject, 1, "Hi", "Hi.", title="Limits: the idea")
    newer_text = session(subject, 2, "Limits again please", "Sure.", title="Review")
    assert [f[:2] for f in found(subject, "limits")] == [
        (by_name.id, True),
        (newer_text.id, False),
        (older_text.id, False),
    ]


def test_untitled_sessions_are_named_by_their_first_message(subject):
    s = session(subject, 0, "What is a derivative?", "Let's see.")
    assert found(subject, "derivative") == [(s.id, True, None)]


def test_snippets_are_plain_text_and_never_cut_math(subject):
    long_math = "$" + r"\int_0^1 " * 12 + "x\\,dx$"
    tutor = (
        "word " * 30
        + long_math
        + " so the squeeze theorem applies here. "
        + "more " * 40
    )
    s = session(subject, 0, "Hello", tutor, block="A boxed example of the squeeze.")
    [(_, in_title, snippet)] = found(subject, "squeeze")
    assert not in_title
    assert snippet.startswith("… ") and snippet.endswith(" …")
    assert long_math in snippet  # widened to the whole formula
    assert "**" not in snippet and "Tutor: word" not in snippet
    assert s.id
    # Blocks are searched without their callout marks.
    [(_, _, snippet)] = found(subject, "boxed")
    assert (
        "[!example]" not in snippet
        and "> " not in snippet
        and "Example A boxed" in snippet
    )


def test_learner_annotations_are_searched(subject):
    s = session(subject, 0, "Hello", "Try it.")
    with s.log_path.open("a", encoding="utf-8") as f:
        f.write("\nMy own note about epsilon-delta proofs.\n")
    assert found(subject, "epsilon-delta")[0][0] == s.id


def test_without_its_log_the_record_is_searched(subject):
    s = session(subject, 0, "Hello", "Continuity means no jumps.")
    s.log_path.unlink()
    assert found(subject, "jumps")[0][0] == s.id


def test_web_search(config, subject):
    s = session(
        subject, 0, "Hello", "We met $\\frac{0}{0}$ forms today.", title="Forms"
    )
    session(subject, 1, "Nothing", "Unrelated.")
    client = TestClient(build_app(config), base_url="http://127.0.0.1:8765")
    [hit] = client.get(
        "/api/subjects/calculus/sessions", params={"q": "met today"}
    ).json()
    assert hit["id"] == s.id and hit["match"] == "text"
    assert hit["snippet"] == "You: Hello Tutor: We met $\\frac{0}{0}$ forms today."
    assert hit["ended"] == "no-wrap-up" and "last_engine" in hit
    assert (
        len(client.get("/api/subjects/calculus/sessions", params={"q": " "}).json())
        == 2
    )


def test_cli_search(config, subject, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: config)
    s = session(subject, 0, "Hello", "Limits at infinity.", title="Asymptotes")
    assert cli.main(["sessions", "calculus", "--search", "infinity"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"{s.id}  Asymptotes  [paused")
    assert lines[1].startswith("    ") and "infinity" in lines[1]
    assert cli.main(["sessions", "calculus", "--search", "nowhere"]) == 0
    assert 'No sessions with "nowhere".' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["sessions", "calculus", "--search", "x", "--remove-empty"])

"""Web chat on the Claude Code engine, with tests/fake_claude.py standing in for Claude Code."""

import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from velocity.chat import IDLE_TIMEOUT, ChatManager
from velocity.claude_engine import ChatEvent, ClaudeCodeChat, parse_event
from velocity.config import create_subject, load_config, load_subject
from velocity.session import Session, active_session_id, record_dir, set_active
from velocity.web import build_app

FAKE = Path(__file__).parent / "fake_claude.py"
ORIGIN = {"Origin": "http://127.0.0.1:8765"}


@pytest.fixture
def config(tmp_path, monkeypatch):
    (tmp_path / "vault").mkdir()
    (tmp_path / "cfg/velocity").mkdir(parents=True)
    (tmp_path / "cfg/velocity/config.toml").write_text(
        f'vault_path = "{tmp_path / "vault"}"\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("FAKE_CLAUDE_SUBJECT", "calculus")
    config = load_config()
    create_subject(config, "Calculus")
    return config


def fake_engine(workspace, model, resume_id, env):
    command = [sys.executable, str(FAKE)] + (
        ["--resume", resume_id] if resume_id else []
    )
    return ClaudeCodeChat(workspace, model, resume_id, env=env, command=command)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client(config, clock):
    manager = ChatManager(config, fake_engine, clock=clock)
    with TestClient(build_app(config, manager), base_url="http://127.0.0.1:8765") as c:
        yield c


def post(client, path, body=None):
    return client.post(
        f"/api/subjects/calculus/chat/{path}", json=body or {}, headers=ORIGIN
    )


def say(client, text) -> list[dict]:
    response = post(client, "message", {"text": text})
    assert response.status_code == 200, response.text
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def session(config, session_id) -> Session:
    subject = load_subject(config, "calculus")
    return Session("calculus", session_id, record_dir("calculus"), subject.sessions_dir)


# -- parsing Claude Code's stream -------------------------------------------------------


def test_parse_event():
    delta = {
        "type": "content_block_delta",
        "delta": {"type": "text_delta", "text": "Hi"},
    }
    assert parse_event(json.dumps({"type": "stream_event", "event": delta})) == [
        ChatEvent("text", text="Hi")
    ]
    tool = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Hi"},
                {
                    "type": "tool_use",
                    "name": "mcp__velocity__record_question",
                    "input": {"a": 1},
                },
            ]
        },
    }
    assert parse_event(json.dumps(tool)) == [
        ChatEvent("tool", name="record_question", input={"a": 1})
    ]
    assert parse_event('{"type": "result", "subtype": "success"}') == [
        ChatEvent("done")
    ]
    failed = parse_event('{"type": "result", "subtype": "error_during_execution"}')
    assert failed[0].type == "error" and "error_during_execution" in failed[0].text
    assert parse_event("not json") == []
    assert parse_event('{"type": "rate_limit_event"}') == []


# -- a whole chat -----------------------------------------------------------------------


def test_chat_logs_streams_and_pauses(client, config):
    assert client.get("/api/subjects/calculus/chat").json()["open"] is False
    assert post(client, "start", {"model": "sonnet"}).json()["open"] is True

    events = say(client, "hi")
    assert [e["type"] for e in events] == ["text", "text", "done"]
    assert "".join(e["text"] for e in events) == "You said: hi"
    tool = say(client, "use a tool")[0]
    assert (tool["type"], tool["name"]) == ("tool", "read_state")

    status = client.get("/api/subjects/calculus/chat").json()
    assert status["open"] and status["session"] and not status["elsewhere"]
    assert [m["text"] for m in status["messages"]][:2] == ["hi", "You said: hi"]

    session_id = status["session"]
    assert post(client, "pause").json()["open"] is False
    paused = session(config, session_id)
    assert paused.ended and not paused.wrapped_up
    assert "**You:** hi\n\n**Tutor:** You said: hi" in paused.log_path.read_text()


def test_end_session_wraps_up_and_closes_the_chat(client, config):
    post(client, "start")
    say(client, "hi")
    session_id = active_session_id("calculus")
    events = say(client, "/end-session")
    assert events[-1] == {
        "type": "ended",
        "text": "wrapped-up",
        "name": "",
        "input": None,
        "cost": None,
    }
    assert session(config, session_id).wrapped_up
    assert client.get("/api/subjects/calculus/chat").json()["open"] is False


def test_crash_is_reported_and_the_session_can_be_resumed(client, config):
    post(client, "start")
    say(client, "hi")
    session_id = active_session_id("calculus")
    events = say(client, "crash")
    assert events[0]["type"] == "error" and "stopped unexpectedly" in events[0]["text"]
    assert events[-1]["type"] == "ended" and events[-1]["text"] == "crashed"
    # A crash leaves the session open; closing it here is the learner's choice.
    assert post(client, "start").status_code == 409
    assert post(client, "start", {"close_other": True}).status_code == 200
    assert session(config, session_id).ended


def test_resume_a_paused_chat_in_place(client, config):
    post(client, "start")
    say(client, "first")
    session_id = active_session_id("calculus")
    post(client, "pause")

    resumed = post(client, "start", {"resume": session_id})
    assert resumed.status_code == 200
    # The earlier conversation is shown at once, before the hooks mark the session active.
    assert resumed.json()["session"] == session_id
    assert [m["text"] for m in resumed.json()["messages"]] == [
        "first",
        "You said: first",
    ]
    say(client, "second")
    assert active_session_id("calculus") == session_id
    text = session(config, session_id).log_path.read_text()
    assert "*Session resumed.*" in text and "**You:** second" in text


def test_resume_a_session_without_a_claude_conversation(client, config):
    # e.g. paused in `velocity chat`: no Claude Code session maps to it.
    subject = load_subject(config, "calculus")
    old = Session.start(
        "calculus", "Calculus", record_dir("calculus"), subject.sessions_dir, []
    )
    old.append("learner", "from the terminal")
    old.render()
    old.end()

    assert post(client, "start", {"resume": old.id}).status_code == 200
    say(client, "back again")
    assert active_session_id("calculus") == old.id
    assert "**You:** back again" in session(config, old.id).log_path.read_text()


def test_wrapped_up_sessions_are_continued_not_resumed(client, config):
    post(client, "start")
    say(client, "hi")
    first = active_session_id("calculus")
    say(client, "/end-session")

    response = post(client, "start", {"resume": first})
    assert (
        response.status_code == 400 and "continued instead" in response.json()["error"]
    )
    assert post(client, "start", {"continues": first}).status_code == 200
    say(client, "more please")
    new = active_session_id("calculus")
    assert new != first and session(config, new).continues == first


def test_one_open_session_per_subject(client, config):
    subject = load_subject(config, "calculus")
    other = Session.start(
        "calculus", "Calculus", record_dir("calculus"), subject.sessions_dir, []
    )
    set_active("calculus", other.id)
    assert client.get("/api/subjects/calculus/chat").json()["elsewhere"] is True
    response = post(client, "start")
    assert response.status_code == 409 and "still open" in response.json()["error"]

    assert post(client, "start", {"close_other": True}).status_code == 200
    assert session(config, other.id).ended
    assert post(client, "start").status_code == 409  # this chat is open now


def test_bad_requests(client):
    assert post(client, "start", {"model": "gpt"}).status_code == 400
    assert post(client, "message", {"text": "hi"}).status_code == 409  # no chat open
    post(client, "start")
    assert post(client, "message", {"text": "  "}).status_code == 400
    assert post(client, "start", {"resume": "2026-01-01-1"}).status_code == 409
    other_page = client.post(
        "/api/subjects/calculus/chat/start",
        json={},
        headers={"Origin": "http://evil.example"},
    )
    assert other_page.status_code == 403


def test_idle_chats_are_paused(client, config, clock):
    post(client, "start")
    say(client, "hi")
    session_id = active_session_id("calculus")
    manager = client.app.state.chats
    clock.now += IDLE_TIMEOUT - 1
    assert client.portal.call(manager.pause_idle) == []
    clock.now += 2
    assert client.portal.call(manager.pause_idle) == ["calculus"]
    assert session(config, session_id).ended


def test_stopping_the_server_pauses_open_chats(config, clock):
    manager = ChatManager(config, fake_engine, clock=clock)
    with TestClient(build_app(config, manager), base_url="http://127.0.0.1:8765") as c:
        post(c, "start")
        say(c, "hi")
        session_id = active_session_id("calculus")
    paused = session(config, session_id)
    assert paused.ended and not paused.wrapped_up

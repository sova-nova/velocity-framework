"""Web chat on the own agent loop (api_engine.py): a scripted API stands in for Claude,
and velocity's real tools run in an in-process MCP server."""

import asyncio
import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from mcp import Client
from starlette.testclient import TestClient

from tests.test_agent import FakeAPI, response, text, tool_use
from velocity import hooks
from velocity.agent import mcp_tool_caller, mcp_tool_params
from velocity.api_engine import ApiChat
from velocity.chat import ChatManager, last_engine
from velocity.claude_engine import ClaudeCodeChat
from velocity.config import create_subject, load_config, load_subject
from velocity.history import RESULT_NOT_RECORDED, rebuild_messages
from velocity.hooks import REMINDER_SHOWN_ENV, REOPEN_ENV
from velocity.mcp_server import build_server
from velocity.session import Session, active_session_id, record_dir
from velocity.tools import TutorTools
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


@pytest.fixture
def api():
    return FakeAPI()


@pytest.fixture
def client(config, api):
    @contextlib.asynccontextmanager
    async def in_process(subject):
        async with Client(build_server(TutorTools(config, subject))) as mcp:
            yield mcp_tool_caller(mcp), mcp_tool_params((await mcp.list_tools()).tools)

    def api_engine(config, subject, model, reopen, continues):
        return ApiChat(
            config, subject, model, reopen, continues, connect=in_process, stream=api
        )

    def claude_engine(workspace, model, resume_id, env):
        c.claude_starts.append((resume_id, env.get(REOPEN_ENV)))
        c.claude_env = env
        command = [sys.executable, str(FAKE)] + (
            ["--resume", resume_id] if resume_id else []
        )
        return ClaudeCodeChat(workspace, model, resume_id, env=env, command=command)

    manager = ChatManager(config, claude_engine, api_engine_factory=api_engine)
    with TestClient(build_app(config, manager), base_url="http://127.0.0.1:8765") as c:
        c.claude_starts = []  # (Claude Code session resumed, velocity session reopened)
        yield c


def post(client, path, body=None):
    return client.post(
        f"/api/subjects/calculus/chat/{path}", json=body or {}, headers=ORIGIN
    )


def say(client, message) -> list[dict]:
    reply = post(client, "message", {"text": message})
    assert reply.status_code == 200, reply.text
    return [
        json.loads(line[len("data: ") :])
        for line in reply.text.splitlines()
        if line.startswith("data: ")
    ]


def session(config, session_id) -> Session:
    subject = load_subject(config, "calculus")
    return Session("calculus", session_id, record_dir("calculus"), subject.sessions_dir)


def paused_sessions(client) -> dict[str, dict]:
    sessions = client.get("/api/subjects/calculus/sessions").json()
    return {s["id"]: s for s in sessions if s["ended"] == "no-wrap-up"}


# -- a whole chat -----------------------------------------------------------------------


def test_api_chat_streams_records_costs_and_resumes(client, config, api):
    started = post(client, "start", {"engine": "api", "model": "sonnet"}).json()
    assert started["open"] and started["engine"] == "api"
    assert started["cost"] == 0
    session_id = started["session"]

    api.results += [response(text("Hello!"))]
    events = say(client, "hi")
    assert "".join(e["text"] for e in events if e["type"] == "text").strip() == "Hello!"
    done = events[-1]
    assert done["type"] == "done"
    assert done["cost"]["reply"] > 0 and done["cost"]["total"] == done["cost"]["reply"]
    assert api.requests[0]["model"] == "claude-sonnet-5-5"

    api.results += [
        response(tool_use("t1", "read_state", {}), stop="tool_use"),
        response(text("You're new here.")),
    ]
    events = say(client, "how am I doing?")
    assert ("tool", "read_state") in [(e["type"], e["name"]) for e in events]
    total = events[-1]["cost"]["total"]
    assert total == pytest.approx(2 * done["cost"]["total"] + done["cost"]["reply"])
    assert client.get("/api/subjects/calculus/chat").json()["cost"] == total

    assert post(client, "pause").json()["open"] is False
    assert paused_sessions(client)[session_id]["last_engine"] == {
        "engine": "api",
        "model": "sonnet",
    }

    # Resume on the own loop: the conversation is rebuilt from the record.
    resumed = post(
        client, "start", {"engine": "api", "model": "opus", "resume": session_id}
    ).json()
    assert resumed["session"] == session_id
    assert [m["text"] for m in resumed["messages"]][:2] == ["hi", "Hello!"]
    api.results += [response(text("Welcome back."))]
    say(client, "again")
    messages = api.requests[-1]["messages"]
    assert messages[0] == {"role": "user", "content": "hi"}
    assert messages[1] == {"role": "assistant", "content": "Hello!"}
    tool_result = messages[4]["content"][0]
    assert tool_result["content"] == RESULT_NOT_RECORDED
    assert any(
        m["role"] == "system" and "restored above" in m["content"] for m in messages
    )
    assert api.requests[-1]["model"] == "claude-opus-5-5"
    assert "*Session resumed.*" in session(config, session_id).log_path.read_text()
    post(client, "pause")
    assert paused_sessions(client)[session_id]["last_engine"]["model"] == "opus"


def test_a_claude_code_session_resumes_on_the_own_loop(client, config, api):
    session_id = post(client, "start", {"model": "sonnet"}).json()["session"]
    say(client, "hi")
    session_id = client.get("/api/subjects/calculus/chat").json()["session"]
    post(client, "pause")
    assert paused_sessions(client)[session_id]["last_engine"] == {
        "engine": "claude-code",
        "model": "sonnet",
    }

    post(client, "start", {"engine": "api", "model": "sonnet", "resume": session_id})
    api.results += [response(text("Where were we?"))]
    say(client, "carry on")
    messages = api.requests[-1]["messages"]
    assert messages[:2] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "You said: hi"},
    ]
    # Claude Code's reply streams no cost; the own loop's does.
    assert client.get("/api/subjects/calculus/chat").json()["cost"] > 0

    # Back on Claude Code: its own conversation lacks the own loop's turns, so it
    # reopens the session and reads the log instead of using --resume.
    post(client, "pause")
    post(client, "start", {"model": "sonnet", "resume": session_id})
    assert client.claude_starts[-1] == (None, session_id)
    say(client, "back on my plan")
    post(client, "pause")
    assert paused_sessions(client)[session_id]["last_engine"]["engine"] == "claude-code"
    # Now Claude Code ran it last, and has a conversation for it: --resume again.
    post(client, "start", {"model": "sonnet", "resume": session_id})
    resumed_id, reopened = client.claude_starts[-1]
    assert resumed_id is not None and reopened is None


def test_page_commands_are_answered_without_the_api(client, api):
    post(client, "start", {"engine": "api", "model": "haiku"})
    for command in ("/model opus", "/frobnicate"):
        events = say(client, command)
        assert [e["type"] for e in events] == ["error", "done"]
    assert api.requests == []


def test_unknown_engine_and_model_are_refused(client):
    assert post(client, "start", {"engine": "gpt"}).status_code == 400
    assert post(client, "start", {"model": "haiku"}).status_code == 400  # Claude Code
    assert post(client, "start", {"engine": "api", "model": "haiku"}).status_code == 200


def test_without_an_api_key_the_own_loop_is_unavailable(config, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with TestClient(build_app(config), base_url="http://127.0.0.1:8765") as c:
        engines = c.get("/api/engines").json()
        assert engines["api"]["available"] is False
        assert "ANTHROPIC_API_KEY" in engines["api"]["reason"]
        assert set(engines["api"]["models"]) == {"sonnet", "opus", "haiku"}
        started = post(c, "start", {"engine": "api", "model": "sonnet"})
        assert started.status_code == 500
        assert "ANTHROPIC_API_KEY" in started.json()["error"]


# -- rebuilding history and the resume defaults ------------------------------------------


def new_session(subject) -> Session:
    return Session.start(
        "calculus", "Calculus", record_dir("calculus"), subject.sessions_dir, []
    )


def test_rebuild_messages_from_a_record(config):
    s = new_session(load_subject(config, "calculus"))
    s.append("learner", "What is a limit?")
    s.append(
        "event", "read_section", kind="tool", meta={"input": {"section_id": "2.2"}}
    )
    s.append("event", "Read", kind="tool", meta={"input": {"path": "x"}})  # not ours
    s.append("event", "record_question", kind="tool", meta={"input": {}, "error": True})
    s.append("block", "lim f(x) = L", title="Definition", kind="example")
    s.append("tutor", "A limit is ...")
    s.append("event", "my own thought", kind="annotation")
    s.append("learner", "Thanks")

    messages = rebuild_messages(s, {"read_section", "record_question"})
    assert messages[0] == {"role": "user", "content": "What is a limit?"}
    assert messages[1]["content"][0]["name"] == "read_section"
    assert messages[2]["content"][0]["content"] == RESULT_NOT_RECORDED
    assert messages[4]["content"][0]["is_error"] is True
    assert messages[5] == {
        "role": "assistant",
        "content": "[Added to the session log: Definition]\nlim f(x) = L\n\n"
        "A limit is ...",
    }
    assert messages[6] == {"role": "user", "content": "Thanks"}
    assert (
        messages[7]["role"] == "system" and "my own thought" in messages[7]["content"]
    )
    assert len(messages) == 8


def test_last_engine_is_inferred_for_older_sessions(config):
    subject = load_subject(config, "calculus")
    s = new_session(subject)
    assert last_engine(subject, s) == {"engine": "api", "model": None}  # velocity chat

    s.append("event", "usage", kind="usage", meta={"model": "claude-haiku-5-5"})
    assert last_engine(subject, s) == {"engine": "api", "model": "haiku"}

    other = new_session(subject)
    mapping = record_dir("calculus") / "claude-sessions"
    mapping.mkdir()
    (mapping / "some-claude-id").write_text(other.id)
    assert last_engine(subject, other) == {"engine": "claude-code", "model": None}


# -- stopping a reply, retrying, and the third-party reminder (PR D) ---------------------


class Hanging:
    """A reply that streams "Once upon" and then never finishes (until cancelled)."""


class HangingStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def _events(self):
        yield NS(type="text", text="Once upon")
        await asyncio.Event().wait()

    def __aiter__(self):
        return self._events()


@pytest.fixture
def hanging_api(api, monkeypatch):
    plain = FakeAPI.__call__

    def call(self, **params):
        if self.results and isinstance(self.results[0], Hanging):
            self.requests.append({**params, "messages": list(params["messages"])})
            self.results.pop(0)
            return HangingStream()
        return plain(self, **params)

    monkeypatch.setattr(FakeAPI, "__call__", call)
    return api


def stop_after_first_text(client, config, message) -> list:
    """Send a message and press Stop as soon as the reply starts."""
    manager = client.app.state.chats
    subject = load_subject(config, "calculus")

    async def run():
        events = []
        async for event in manager.send(subject, message):
            events.append(event)
            if event.type == "text":
                await manager.stop_reply(subject)
        return events

    return client.portal.call(run)


def log_of(client, config) -> str:
    session_id = client.get("/api/subjects/calculus/chat").json()["session"]
    return session(config, session_id).log_path.read_text()


def test_stop_keeps_the_partial_reply_on_claude_code(client, config):
    post(client, "start", {"model": "sonnet"})
    say(client, "hi")
    events = stop_after_first_text(client, config, "write at length")
    assert [e.type for e in events] == ["text", "stopped"]
    assert "**Tutor:** Once upon\n\n*(stopped)*" in log_of(client, config)
    # The process is still there for the next message.
    assert "".join(e["text"] for e in say(client, "next")) == "You said: next"


def test_stop_keeps_the_partial_reply_on_the_own_loop(client, config, hanging_api):
    post(client, "start", {"engine": "api", "model": "sonnet"})
    hanging_api.results += [Hanging()]
    events = stop_after_first_text(client, config, "write at length")
    assert [e.type for e in events] == ["text", "stopped"]
    assert events[-1].cost is not None
    assert "**Tutor:** Once upon\n\n*(stopped)*" in log_of(client, config)

    hanging_api.results += [response(text("Sure."))]
    say(client, "go on")
    messages = hanging_api.requests[-1]["messages"]
    # The stopped reply stays in the history, right before the next message.
    assert messages[-2:] == [
        {
            "role": "assistant",
            "content": "Once upon\n\n[The learner stopped this reply here.]",
        },
        {"role": "user", "content": "go on"},
    ]


def test_a_retried_message_is_logged_once(config):
    subject = load_subject(config, "calculus")
    hooks.session_start(config, subject, {"session_id": "c1", "source": "startup"})
    for _ in range(2):  # the first reply failed, so the learner retries
        hooks.user_prompt(config, subject, {"prompt": "What is a limit?"})
    hooks.stop(config, subject, {"last_assistant_message": "A value approached."})
    hooks.user_prompt(config, subject, {"prompt": "What is a limit?"})  # asked again
    s = session(config, active_session_id("calculus"))
    learner = [e.text for e in s.events() if e.role == "learner"]
    assert learner == ["What is a limit?", "What is a limit?"]


def test_web_chats_tell_the_tutor_the_reminder_was_shown(client, config, api):
    subject = load_subject(config, "calculus")
    context = hooks.session_start(
        config, subject, {"session_id": "c1", "source": "startup"}
    )["hookSpecificOutput"]["additionalContext"]
    assert "Open with the third-party reminder" in context
    hooks.session_end(config, subject, {"reason": "other"})

    post(client, "start", {"model": "sonnet"})
    assert client.claude_env[REMINDER_SHOWN_ENV] == "1"
    post(client, "pause")

    post(client, "start", {"engine": "api", "model": "sonnet"})
    api.results += [response(text("Hello!"))]
    say(client, "hi")
    system = [
        m["content"] for m in api.requests[-1]["messages"] if m["role"] == "system"
    ]
    assert any("already seen the third-party reminder" in s for s in system)
    assert not any("Open with the third-party reminder" in s for s in system)

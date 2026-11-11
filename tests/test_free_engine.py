"""The free engine (openai_engine.py): a scripted OpenAI-compatible server stands in for
the model, and velocity's real tools run in an in-process MCP server."""

import asyncio
import contextlib
import json
from types import SimpleNamespace as NS

import pytest
from mcp import Client
from starlette.testclient import TestClient

from velocity import openai_engine
from velocity.agent import mcp_tool_caller, mcp_tool_params
from velocity.chat import ChatManager, engines
from velocity.config import (
    ConfigError,
    create_subject,
    load_config,
    load_subject,
    save_settings,
)
from velocity.engine import FREE
from velocity.history import HistoryBuilder
from velocity.mcp_server import build_server
from velocity.openai_engine import (
    LONG_CHAT_NOTE,
    MAX_TOOL_RETRIES,
    TOOL_FAILED_NOTE,
    TRIMMED_NOTE,
    FreeChat,
    ThinkFilter,
    check_model,
    convert_history,
    is_local,
    openai_tools,
    privacy_note,
    unavailable_reason,
)
from velocity.session import Session, record_dir
from velocity.state import load_state
from velocity.tools import TutorTools
from velocity.web import build_app

ORIGIN = {"Origin": "http://127.0.0.1:8765"}


# -- a scripted server ------------------------------------------------------------------


def chunk(content=None, calls=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=calls))])


def call(index, name, arguments, call_id=None):
    return NS(index=index, id=call_id, function=NS(name=name, arguments=arguments))


class FakeServer:
    """`client.chat.completions.create(stream=True, ...)`: each request gets the next
    scripted reply, a list of chunks. Requests are kept, deep-copied."""

    def __init__(self) -> None:
        self.replies: list[list] = []
        self.requests: list[dict] = []
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **params):
        self.requests.append(json.loads(json.dumps(params)))
        if not self.replies:
            raise AssertionError("no scripted reply left")
        chunks = self.replies.pop(0)

        async def stream():
            for c in chunks:
                yield c

        return stream()


def says(*texts):
    return [chunk(t) for t in texts]


@pytest.fixture
def config(tmp_path, monkeypatch):
    (tmp_path / "vault").mkdir()
    (tmp_path / "cfg/velocity").mkdir(parents=True)
    (tmp_path / "cfg/velocity/config.toml").write_text(
        f'vault_path = "{tmp_path / "vault"}"\n\n'
        '[openai]\nbase_url = "http://localhost:11434/v1"\nmodel = "qwen3:4b"\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    config = load_config()
    create_subject(config, "Calculus")
    return config


@pytest.fixture
def server():
    return FakeServer()


@pytest.fixture
def client(config, server):
    @contextlib.asynccontextmanager
    async def in_process(subject):
        async with Client(build_server(TutorTools(config, subject))) as mcp:
            yield mcp_tool_caller(mcp), mcp_tool_params((await mcp.list_tools()).tools)

    def free_engine(config, subject, reopen, continues):
        return FreeChat(config, subject, reopen, continues, in_process, server)

    manager = ChatManager(config, free_engine_factory=free_engine)
    with TestClient(build_app(config, manager), base_url="http://127.0.0.1:8765") as c:
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


def shown(events) -> str:
    return "".join(e["text"] for e in events if e["type"] == "text").strip()


def log_of(config, session_id) -> str:
    subject = load_subject(config, "calculus")
    session = Session(
        "calculus", session_id, record_dir("calculus"), subject.sessions_dir
    )
    session.render()
    return session.log_path.read_text(encoding="utf-8")


# -- a whole chat -------------------------------------------------------------------------


def test_free_chat_streams_logs_records_and_has_no_cost(client, config, server):
    started = post(client, "start", {"engine": FREE, "model": "qwen3:4b"}).json()
    assert started["open"] and started["engine"] == FREE and started["cost"] is None
    session_id = started["session"]

    server.replies += [says("<think>The learner", " greets me.</think>", "Hello!")]
    events = say(client, "hi")
    assert shown(events) == "Hello!"  # the thinking is never shown
    assert events[-1] == {
        "type": "done",
        "text": "",
        "name": "",
        "input": None,
        "cost": None,
    }
    request = server.requests[0]
    assert request["model"] == "qwen3:4b" and request["stream"] is True
    system, learner = request["messages"]
    assert system["role"] == "system" and "tutor" in system["content"]
    # velocity's notes for the tutor go in the learner's message, not a system message.
    assert learner["role"] == "user" and learner["content"].startswith(
        "hi\n\n<velocity-note>"
    )
    names = {t["function"]["name"] for t in request["tools"]}
    assert {"record_question", "load_skill"} <= names and "view_page" not in names

    server.replies += [
        [
            chunk(calls=[call(0, "record_question", '{"topic": "Lim', "c1")]),
            chunk(calls=[call(0, "", 'its", "question": "Q", "outcome": "partial"}')]),
        ],
        says("◐ Partly right."),
    ]
    events = say(client, "my answer")
    assert [e["name"] for e in events if e["type"] == "tool"] == ["record_question"]
    assert shown(events) == "◐ Partly right."
    tool_result = server.requests[2]["messages"][-1]
    assert tool_result["role"] == "tool" and tool_result["tool_call_id"] == "c1"
    assert "Recorded (partial)" in tool_result["content"]
    assert (
        load_state(load_subject(config, "calculus").dir).questions[-1].outcome
        == "partial"
    )

    log = log_of(config, session_id)
    assert "**You:** hi" in log and "Hello!" in log and "The learner greets" not in log
    assert "◐ Recorded: partial · Limits" in log


def test_a_paused_free_chat_resumes_with_its_history(client, config, server):
    session_id = post(client, "start", {"engine": FREE, "model": "qwen3:4b"}).json()[
        "session"
    ]
    server.replies += [says("Limits are about approaching.")]
    say(client, "what is a limit?")
    post(client, "pause")

    resumed = post(
        client, "start", {"engine": FREE, "model": "an-old-model", "resume": session_id}
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["model"] == "qwen3:4b"  # the model in Settings now
    server.replies += [says("Welcome back.")]
    say(client, "hello again")
    history = server.requests[-1]["messages"]
    assert [m["role"] for m in history] == ["system", "user", "assistant", "user"]
    assert history[1]["content"].startswith("what is a limit?")
    assert history[2]["content"] == "Limits are about approaching."


def test_bad_tool_calls_go_back_to_the_model_then_the_turn_ends(client, server):
    post(client, "start", {"engine": FREE, "model": "qwen3:4b"})
    bad = [chunk("Let me record that.", [call(0, "record_question", "{not json", "b")])]
    server.replies += [bad] * (MAX_TOOL_RETRIES + 1)
    events = say(client, "answer")
    assert events[-2] == {**events[-2], "type": "note", "text": TOOL_FAILED_NOTE}
    assert events[-1]["type"] == "done"
    # Each bad call was answered with an error the model could act on.
    retry = server.requests[1]["messages"][-1]
    assert retry["role"] == "tool" and "not a JSON object" in retry["content"]
    # The history stays valid for the next turn: it ends with an assistant message.
    server.replies += [says("OK.")]
    say(client, "next")
    assert server.requests[-1]["messages"][-2]["role"] == "assistant"


def test_an_unknown_tool_is_an_error_for_the_model(client, server):
    post(client, "start", {"engine": FREE, "model": "qwen3:4b"})
    server.replies += [
        [chunk(calls=[call(0, "make_coffee", "{}", "x")])],
        says("Sorry."),
    ]
    events = say(client, "hi")
    assert not [e for e in events if e["type"] == "tool"]
    assert (
        "no tool named 'make_coffee'" in server.requests[1]["messages"][-1]["content"]
    )


def test_a_long_chat_gets_a_note_then_its_oldest_turns_are_left_out(
    config, server, monkeypatch
):
    subject = load_subject(config, "calculus")
    agent = openai_engine.FreeAgent(
        config=config,
        subject=subject,
        create=server.create,
        call_tool=None,
        tools=[],
        skills={},
        model="m",
        context_size=8_192,
    )
    events = []
    agent.on_event = events.append
    agent.start()
    long = "x" * 8_000  # about 2,000 tokens a message
    for n in range(4):
        server.replies += [says(f"reply {n}")]
        asyncio.run(agent.turn(f"turn {n} {long}"))
    notes = [e.text for e in events if e.type == "note"]
    assert LONG_CHAT_NOTE in notes and TRIMMED_NOTE in notes
    sent = server.requests[-1]["messages"]
    learner = [m["content"] for m in sent if m["role"] == "user"]
    assert learner[0].startswith("turn 0")  # the first turn, with the session's start
    assert learner[-1].startswith("turn 3")
    assert not any(t.startswith("turn 1") for t in learner)
    agent.finish()


# -- pieces -------------------------------------------------------------------------------


def test_think_filter_drops_thinking_split_across_chunks():
    f = ThinkFilter()
    parts = ["Hi <th", "ink>secret", " plan</thi", "nk>  there", " <", "b>"]
    assert "".join(f.feed(p) for p in parts) + f.flush() == "Hi there <b>"


def test_history_from_a_record_in_the_openai_shape():
    b = HistoryBuilder()
    b.learner("Is it odd?", ["A note."])
    b.tool("record_question", {"topic": "odd"})
    b.tutor("Yes.")
    messages = convert_history(b.messages)
    assert messages[0] == {
        "role": "user",
        "content": "Is it odd?\n\n<velocity-note>\nA note.\n</velocity-note>",
    }
    assistant, result, reply = messages[1:]
    assert assistant["tool_calls"][0]["function"] == {
        "name": "record_question",
        "arguments": '{"topic": "odd"}',
    }
    assert result["role"] == "tool"
    assert result["tool_call_id"] == assistant["tool_calls"][0]["id"]
    assert reply == {"role": "assistant", "content": "Yes."}


def test_view_page_only_for_models_that_see_images():
    tools = [
        {"name": "view_page", "description": "", "input_schema": {}},
        {"name": "read_section", "description": "", "input_schema": {}},
    ]
    assert [t["function"]["name"] for t in openai_tools(tools, False)] == [
        "read_section"
    ]
    assert len(openai_tools(tools, True)) == 2


def test_privacy_note_says_where_things_go(config):
    assert is_local("http://127.0.0.1:8080/v1") and is_local("http://localhost:11434")
    assert not is_local("https://openrouter.ai/api/v1")
    assert "on your computer" in privacy_note(config)
    hosted = config.__class__(
        config.vault_path, openai_base_url="https://openrouter.ai/api/v1"
    )
    assert "openrouter.ai" in privacy_note(hosted) and "training" in privacy_note(
        hosted
    )


def test_unavailable_until_set_up_and_with_the_key_variable(config, monkeypatch):
    assert unavailable_reason(config) is None
    assert "Settings" in unavailable_reason(config.__class__(config.vault_path))
    keyed = config.__class__(
        config.vault_path,
        openai_base_url="https://openrouter.ai/api/v1",
        openai_model="m",
        openai_api_key_env="OPENROUTER_API_KEY",
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert "OPENROUTER_API_KEY is not set" in unavailable_reason(keyed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert unavailable_reason(keyed) is None
    assert openai_engine.api_key(keyed) == "k"


def test_engines_list_the_free_engine(config):
    free = engines(config)[FREE]
    assert free["available"] and free["models"] == {"qwen3:4b": "qwen3:4b"}
    assert free["label"] == openai_engine.LABEL and "mistakes" in free["note"]
    assert "on your computer" in free["reminder"]
    assert not engines(None)[FREE]["available"]


def test_check_model_reports_tool_use(config, monkeypatch):
    async def no_ollama(config):
        return 2_048

    monkeypatch.setattr(openai_engine, "ollama_context", no_ollama)

    def client(calls):
        async def create(**params):
            return NS(choices=[NS(message=NS(tool_calls=calls, content="ok"))])

        return NS(chat=NS(completions=NS(create=create)))

    used = NS(function=NS(name="check", arguments='{"word": "ok"}'))
    ok = asyncio.run(check_model(config, client([used])))
    assert ok["ok"] and ok["tools"]
    assert "OLLAMA_CONTEXT_LENGTH=32768" in ok["message"]  # Ollama loaded only 2,048
    plain = asyncio.run(check_model(config, client(None)))
    assert plain["ok"] and not plain["tools"] and "can't use tools" in plain["message"]


# -- settings -----------------------------------------------------------------------------


def test_free_engine_settings_are_saved_in_their_own_table(config):
    save_settings({"api_model": "sonnet"})
    saved = save_settings(
        {
            "openai_base_url": "https://openrouter.ai/api/v1",
            "openai_model": "some/model:free",
            "openai_api_key_env": "OPENROUTER_API_KEY",
            "openai_context_size": "16384",
            "openai_images": True,
        }
    )
    assert saved.api_model == "sonnet"  # [api] model and [openai] model don't mix
    assert saved.openai_model == "some/model:free"
    assert saved.openai_api_key_env == "OPENROUTER_API_KEY"
    assert (saved.openai_context_size, saved.openai_images) == (16_384, True)
    cleared = save_settings({"openai_images": False, "openai_api_key_env": ""})
    assert not cleared.openai_images and cleared.openai_api_key_env is None
    with pytest.raises(ConfigError, match="environment variable"):
        save_settings({"openai_api_key_env": "sk-or-v1-abc def"})
    with pytest.raises(ConfigError, match="at least"):
        save_settings({"openai_context_size": "2048"})
    with pytest.raises(ConfigError, match="http"):
        save_settings({"openai_base_url": "localhost:11434"})


def test_the_tool_server_gets_the_whole_environment(monkeypatch):
    """Found 2026-11-11: the MCP SDK passes only a few variables by default, so the
    server missed XDG_* (and read another config) and LLAMA_CPP_BINARY."""
    import mcp

    from velocity.api_engine import connect_mcp

    seen = {}

    class Stop(Exception):
        pass

    def params(**kwargs):
        seen.update(kwargs)
        raise Stop

    monkeypatch.setattr(mcp, "StdioServerParameters", params)
    monkeypatch.setenv("LLAMA_CPP_BINARY", "/opt/llama-server")

    async def run():
        async with connect_mcp(NS(slug="calculus")):
            pass

    with pytest.raises(Stop):
        asyncio.run(run())
    assert seen["env"]["LLAMA_CPP_BINARY"] == "/opt/llama-server"
    assert seen["env"]["XDG_DATA_HOME"]

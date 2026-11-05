import asyncio
import io
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from velocity.agent import (
    FALLBACK_BETA,
    TutorAgent,
    Usage,
    UsageByModel,
    echo_content,
    mcp_tool_params,
    resolve_model,
    system_prompt,
)
from velocity.config import Config, Subject
from velocity.skills import load_skills, parse_slash_command
from velocity.tools import ToolError, TutorTools


def text(t):
    return NS(type="text", text=t)


def tool_use(id_, name, input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


def response(*blocks, stop="end_turn"):
    usage = NS(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=1000,
        cache_creation_input_tokens=0,
    )
    return NS(content=list(blocks), stop_reason=stop, usage=usage)


class FakeStream:
    def __init__(self, result):
        self.result = result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def _events(self):
        if isinstance(self.result, Exception):
            raise self.result
        for block in self.result.content:
            if block.type == "text":
                yield NS(type="text", text=block.text)

    def __aiter__(self):
        return self._events()

    async def get_final_message(self):
        return self.result


class FakeAPI:
    """Stands in for client.beta.messages.stream; replays scripted results in order."""

    def __init__(self, *results):
        self.results = list(results)
        self.requests: list[dict] = []

    def __call__(self, **params):
        self.requests.append({**params, "messages": list(params["messages"])})
        return FakeStream(self.results.pop(0))


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.fixture
def config(tmp_path) -> Config:
    (tmp_path / "vault").mkdir()
    return Config((tmp_path / "vault").resolve())


@pytest.fixture
def subject(config) -> Subject:
    return Subject("calculus", "Calculus", config.tutor_dir / "calculus", "", (), ())


def make_agent(config, subject, api, model="claude-opus-5-5"):
    tools = TutorTools(config, subject)

    async def call_tool(name, args):
        try:
            return getattr(tools, name)(**args), False
        except ToolError as e:
            return str(e), True

    agent = TutorAgent(
        config=config,
        subject=subject,
        stream=api,
        call_tool=call_tool,
        tools=[{"name": "record_question"}],
        skills=load_skills(),
        out=io.StringIO(),
        model=model,
    )
    agent.start()
    return agent


def log_text(agent) -> str:
    return Path(agent.log_path).read_text() if agent.log_path else ""


def test_turn_with_a_tool_call_is_logged_and_recorded(config, subject):
    api = FakeAPI(
        response(
            text("Let me note that."),
            tool_use(
                "t1",
                "record_question",
                {"topic": "Limits", "question": "q", "outcome": "correct"},
            ),
            stop="tool_use",
        ),
        response(text("Correct! Next question...")),
    )
    agent = make_agent(config, subject, api)
    reply = asyncio.run(agent.turn("The limit is 3."))

    assert reply == "Let me note that.\n\nCorrect! Next question..."
    first, second = api.requests
    assert [m["role"] for m in first["messages"]] == ["user", "system"]
    assert (
        "velocity session" in first["messages"][1]["content"]
    )  # session-start context
    assert [m["role"] for m in second["messages"]] == [
        "user",
        "system",
        "assistant",
        "user",
    ]
    result = second["messages"][3]["content"][0]
    assert result["tool_use_id"] == "t1" and "Recorded (correct)" in result["content"]
    assert "is_error" not in result
    log = log_text(agent)
    assert "**You:** The limit is 3." in log and "**Tutor:** Let me note that." in log
    assert (subject.dir / "progress.md").exists()
    assert agent.usage.requests == 2


def test_request_parameters(config, subject):
    api = FakeAPI(response(text("hi")))
    asyncio.run(make_agent(config, subject, api).turn("hello"))
    params = api.requests[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["cache_control"] == {"type": "ephemeral"}
    assert params["output_config"] == {"effort": "medium"}
    assert params["fallbacks"] == "default" and params["betas"] == [FALLBACK_BETA]
    assert "# You are a tutor for: Calculus" in params["system"]
    assert "`quiz`:" in params["system"]  # skill catalogue

    api = FakeAPI(response(text("hi")))
    asyncio.run(
        make_agent(config, subject, api, model="claude-haiku-5-5").turn("hello")
    )
    assert "fallbacks" not in api.requests[0] and "betas" not in api.requests[0]


def test_slash_command_injects_skill_instructions(config, subject):
    api = FakeAPI(response(text("Question 1 of 3: ...")))
    asyncio.run(make_agent(config, subject, api).turn("/quiz limits 3"))
    systems = [
        m["content"] for m in api.requests[0]["messages"] if m["role"] == "system"
    ]
    assert any(
        "invoked the skill `quiz` with arguments: limits 3" in s and "# Quiz" in s
        for s in systems
    )


def test_load_skill_tool(config, subject):
    api = FakeAPI(
        response(
            tool_use("t1", "load_skill", {"name": "socratic"}),
            tool_use("t2", "load_skill", {"name": "nope"}),
            stop="tool_use",
        ),
        response(text("What do you already know?")),
    )
    asyncio.run(make_agent(config, subject, api).turn("lead me to it"))
    good, bad = api.requests[1]["messages"][-1]["content"]
    assert "# Socratic mode" in good["content"] and "is_error" not in good
    assert bad["is_error"] and "No skill named 'nope'" in bad["content"]


def test_refusal_rolls_back_and_keeps_context_for_next_turn(config, subject):
    api = FakeAPI(response(stop="refusal"), response(text("Sure.")))
    agent = make_agent(config, subject, api)
    asyncio.run(agent.turn("first"))
    assert agent.messages == []
    asyncio.run(agent.turn("second"))
    roles = [(m["role"], m["content"][:16]) for m in api.requests[1]["messages"]]
    assert roles[0] == ("user", "second") and roles[1][0] == "system"
    assert "velocity session" in api.requests[1]["messages"][1]["content"]


def test_api_error_rolls_back_and_reraises(config, subject):
    api = FakeAPI(ConnectionError("down"), response(text("ok")))
    agent = make_agent(config, subject, api)
    with pytest.raises(ConnectionError):
        asyncio.run(agent.turn("hello"))
    assert agent.messages == []
    asyncio.run(agent.turn("hello again"))
    assert [m["role"] for m in agent.messages] == ["user", "system", "assistant"]


def test_unparseable_tool_json_is_retried(config, subject):
    api = FakeAPI(ValueError("bad json"), response(text("ok")))
    agent = make_agent(config, subject, api)
    assert asyncio.run(agent.turn("hi")) == "ok"
    assert len(api.requests) == 2


def test_end_session_closes_the_log_and_ends_the_chat(config, subject):
    api = FakeAPI(
        response(
            tool_use("t1", "end_session", {"recap": "- covered limits"}),
            stop="tool_use",
        ),
        response(text("Great work. You can exit now.")),
    )
    agent = make_agent(config, subject, api)
    path = agent.log_path
    asyncio.run(agent.turn("/end-session"))
    assert agent.ended
    log = Path(path).read_text()
    assert "> [!summary] Session recap" in log
    assert log.endswith("*Session ended with /end-session.*\n")


def test_finish_closes_an_open_log(config, subject):
    agent = make_agent(config, subject, FakeAPI(response(text("hi"))))
    path = agent.log_path
    asyncio.run(agent.turn("hello"))
    agent.finish()
    assert Path(path).read_text().endswith("*Session ended without wrap-up.*\n")


def test_echo_content_after_mid_output_fallback():
    thinking, partial = NS(type="thinking"), text("partial")
    content = [
        thinking,
        partial,
        NS(type="tool_use"),
        NS(type="fallback"),
        NS(type="thinking"),
        text("rest"),
    ]
    assert [b.type for b in echo_content(content)] == ["text", "thinking", "text"]
    assert echo_content([thinking, partial]) == [thinking, partial]


def test_usage_cost():
    u = Usage()
    u.add(
        NS(
            input_tokens=1_000_000,
            output_tokens=100_000,
            cache_read_input_tokens=1_000_000,
            cache_creation_input_tokens=0,
        )
    )
    assert u.cost("claude-opus-5-5") == pytest.approx(4.0 + 2.0 + 0.2)
    assert u.cost("some-other-model") is None
    assert "about $6.20" in u.summary("claude-opus-5-5")


def test_tool_params_are_sorted_and_include_load_skill():
    tools = [
        NS(name="read_state", description="d", input_schema={"type": "object"}),
        NS(name="append_log", description=None, input_schema={"type": "object"}),
    ]
    params = mcp_tool_params(tools)
    assert [p["name"] for p in params] == ["append_log", "read_state", "load_skill"]
    assert all(p["eager_input_streaming"] for p in params)


def test_slash_command_parsing():
    skills = load_skills()
    assert parse_slash_command("/quiz", skills)[1] == ""
    assert parse_slash_command("/assign-problems 2.4 3", skills)[1] == "2.4 3"
    assert parse_slash_command("/unknown", skills) is None
    assert parse_slash_command("what is /quiz", skills) is None
    assert "review-notes" in system_prompt(
        Subject("x", "X", Path("."), "", (), ()), skills
    )


def test_resolve_model():
    assert resolve_model("sonnet") == "claude-sonnet-5-5"
    assert resolve_model(" Haiku ") == "claude-haiku-5-5"
    assert resolve_model("claude-opus-5") == "claude-opus-5"
    with pytest.raises(ValueError, match="Unknown model"):
        resolve_model("gpt-5")


def test_model_command_switches_the_model_for_later_requests(config, subject):
    api = FakeAPI(response(text("one")), response(text("two")))
    agent = make_agent(config, subject, api)
    asyncio.run(agent.turn("hello"))

    assert "Current model: claude-opus-5-5" in agent.model_command("")
    assert "Unknown model" in agent.model_command("gpt")
    assert agent.model == "claude-opus-5-5"
    assert "Already using" in agent.model_command("opus")
    assert "Switched to claude-haiku-5-5" in agent.model_command("haiku")

    asyncio.run(agent.turn("again"))
    second = api.requests[1]
    assert second["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in second  # Haiku has no server-side fallback
    assert (
        second["messages"][: len(api.requests[0]["messages"])]
        == api.requests[0]["messages"]
    )  # history kept as is
    assert set(agent.usage.models) == {"claude-opus-5-5", "claude-haiku-5-5"}
    assert "/model" not in log_text(agent)  # not part of the dialogue


def test_usage_by_model_summary():
    usage = UsageByModel()
    assert usage.summary() == "No API requests."
    tokens = NS(
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    usage.add("claude-opus-5-5", tokens)
    assert usage.summary().startswith("claude-opus-5-5: 1 API requests")
    assert "Total" not in usage.summary()
    usage.add("claude-sonnet-5-5", tokens)
    assert usage.requests == 2
    assert "Total: about $6.00" in usage.summary()

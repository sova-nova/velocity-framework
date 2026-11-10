import asyncio
import io
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from velocity.agent import (
    COMPACT_AT,
    COMPACT_BETA,
    COMPACTED_NOTE,
    FALLBACK_BETA,
    TutorAgent,
    Usage,
    UsageByModel,
    echo_content,
    mcp_tool_params,
    resolve_model,
    system_prompt,
    unknown_command,
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
            if block.type == "compaction":
                yield NS(type="content_block_start", content_block=block)
            elif block.type == "text":
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
    tools = [e for e in _session_events(subject) if e.kind == "tool"]
    assert [(e.text, e.meta) for e in tools] == [
        (
            "record_question",
            {"input": {"topic": "Limits", "question": "q", "outcome": "correct"}},
        )
    ]


def test_request_parameters(config, subject):
    api = FakeAPI(response(text("hi")))
    asyncio.run(make_agent(config, subject, api).turn("hello"))
    params = api.requests[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["cache_control"] == {"type": "ephemeral"}
    assert params["output_config"] == {"effort": "medium"}
    assert params["fallbacks"] == "default"
    assert params["betas"] == [FALLBACK_BETA, COMPACT_BETA]
    (edit,) = params["context_management"]["edits"]
    assert edit["type"] == "compact_20260112"
    assert edit["trigger"] == {"type": "input_tokens", "value": COMPACT_AT}
    assert "tutoring session" in edit["instructions"]
    assert "# You are a tutor for: Calculus" in params["system"]
    assert "`quiz`:" in params["system"]  # skill catalogue

    # Haiku: compaction, no fallbacks.
    api = FakeAPI(response(text("hi")))
    asyncio.run(
        make_agent(config, subject, api, model="claude-haiku-5-5").turn("hello")
    )
    assert "fallbacks" not in api.requests[0]
    assert api.requests[0]["betas"] == [COMPACT_BETA]

    # A model without compaction gets neither.
    api = FakeAPI(response(text("hi")))
    asyncio.run(
        make_agent(config, subject, api, model="claude-haiku-4-5").turn("hello")
    )
    assert "betas" not in api.requests[0]
    assert "context_management" not in api.requests[0]


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


def test_unknown_commands_are_answered_locally():
    skills = load_skills()
    assert unknown_command("/haiku", skills) == (
        "Unknown command /haiku. To switch models, type /model haiku."
    )
    message = unknown_command("/foo bar", skills)
    assert message.startswith("Unknown command /foo. Commands: ")
    assert "/quiz" in message and "/model" in message
    for known in ["/quiz limits 3", "/model haiku", "/exit", "/end-session"]:
        assert unknown_command(known, skills) is None
    for text in ["what is /quiz", "/usr/bin/env", "/ 2", "hello"]:
        assert unknown_command(text, skills) is None


def _session_events(subject):
    from velocity.session import Session, active_session_id, record_dir

    return Session(
        subject.slug,
        active_session_id(subject.slug),
        record_dir(subject.slug),
        subject.sessions_dir,
    ).events()


def test_failed_tool_calls_are_recorded_as_errors(config, subject):
    api = FakeAPI(
        response(tool_use("t1", "load_skill", {"name": "nope"}), stop="tool_use"),
        response(text("ok")),
    )
    agent = make_agent(config, subject, api)
    asyncio.run(agent.turn("hi"))
    tools = [e for e in _session_events(subject) if e.kind == "tool"]
    assert tools[0].meta == {"input": {"name": "nope"}, "error": True}


def test_compaction_is_shown_kept_and_counted(config, subject):
    summary = NS(type="compaction", content="The learner is on one-sided limits.")
    compacted = response(summary, text("Where were we? One-sided limits."))
    compacted.usage.iterations = [
        NS(
            type="compaction",
            input_tokens=150_000,
            output_tokens=2_000,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        NS(
            type="message",
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=1000,
            cache_creation_input_tokens=0,
        ),
    ]
    api = FakeAPI(compacted, response(text("Next question...")))
    agent = make_agent(config, subject, api)
    events = []
    agent.on_event = events.append
    asyncio.run(agent.turn("Go on."))

    assert ("note", COMPACTED_NOTE) in [(e.type, e.text) for e in events]
    assert COMPACTED_NOTE in agent.out.getvalue()
    # The summary goes back to the API with the reply, so the next request uses it.
    assert agent.messages[-1]["content"][0] is summary
    # The compaction step is counted in the cost.
    usage = agent.last_turn_usage
    assert (usage.input, usage.output, usage.cache_read) == (150_100, 2_050, 1000)
    # Only the reply is logged, not the summary.
    assert "One-sided limits." in log_text(agent)
    assert "The learner is on" not in log_text(agent)

    asyncio.run(agent.turn("ok"))
    assert any(
        isinstance(m["content"], list) and summary in m["content"]
        for m in api.requests[1]["messages"]
    )


def test_stop_keeps_a_compaction_summary(config, subject):
    agent = make_agent(config, subject, FakeAPI())
    summary = NS(type="compaction", content="Summary so far.")
    agent.messages = [
        {"role": "user", "content": "Go on."},
        {"role": "assistant", "content": [summary, tool_use("t1", "read", {})]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1"}]},
    ]
    agent._streamed = "Once upon"
    agent._keep_stopped(1)

    (reply,) = agent.messages[1:]
    assert reply["content"] == [
        summary,
        {"type": "text", "text": "Once upon\n\n[The learner stopped this reply here.]"},
    ]


def test_echo_content_keeps_compaction_before_a_fallback():
    summary = NS(type="compaction", content="s")
    content = [summary, NS(type="thinking"), NS(type="fallback"), text("hi")]
    assert echo_content(content) == [summary, content[3]]

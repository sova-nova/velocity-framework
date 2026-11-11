import asyncio
import json
import re
from datetime import date
from types import SimpleNamespace as NS

import pytest

from evals.cases import DATA_DIR, Case, load_cases, replay_input, state_before
from evals.checks import run_checks
from evals.replay import SkipCase, replay
from evals.run import format_outcome, run_all, select_cases, summary

TODAY = date(2026, 12, 1)


def text(t):
    return NS(type="text", text=t)


def tool_use(id_, name, input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


def response(*blocks, stop="end_turn"):
    usage = NS(
        input_tokens=1000,
        output_tokens=100,
        cache_read_input_tokens=0,
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
        for block in self.result.content:
            if block.type == "text":
                yield NS(type="text", text=block.text)

    def __aiter__(self):
        return self._events()

    async def get_final_message(self):
        return self.result


class FakeAPI:
    def __init__(self, *results):
        self.results = list(results)
        self.requests: list[dict] = []

    def __call__(self, **params):
        self.requests.append({**params, "messages": list(params["messages"])})
        return FakeStream(self.results.pop(0))


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def case(**raw) -> Case:
    raw = {"id": "x", **raw}
    return Case(raw["id"], "", raw.get("expect", {}), raw)


# -- cases ------------------------------------------------------------------------------


def test_all_cases_load_and_their_expectations_are_valid():
    cases = load_cases()
    assert len(cases) >= 30
    for c in cases:
        inp = replay_input(c)
        assert inp.message.strip()
        run_checks(c.expect, "", [])  # raises on unknown expectation keys


def test_committed_records_contain_no_home_paths():
    for path in (DATA_DIR / "records").rglob("*.jsonl"):
        assert "/home/" not in path.read_text(), path


def test_replay_input_from_a_record_splits_history_and_pending_annotations():
    inp = replay_input(case(session="2026-11-05-1", turn=4))
    assert inp.message == "I rewrote it."
    assert inp.sources == ("Stewart",)
    assert inp.date == date(2026, 11, 5)
    assert inp.previous_sessions == tuple(f"2026-11-04-{n}" for n in range(1, 7))
    roles = [t.role for t in inp.history]
    assert roles == [
        "learner",
        "tutor",
        "learner",
        "tutor",
        "annotation",
        "learner",
        "tutor",
    ]
    assert inp.history[4].text.startswith("That limit means")
    assert len(inp.annotations) == 1 and inp.annotations[0].startswith("No, since you")


def test_case_annotations_override_the_record():
    inp = replay_input(
        case(session="2026-11-04-1", turn=9, annotations=["It approaches 4."])
    )
    assert inp.annotations == ("It approaches 4.",)


def test_missing_turn_is_an_error():
    with pytest.raises(ValueError, match="no learner turn 99"):
        replay_input(case(session="2026-11-04-1", turn=99))


def test_state_before_replays_earlier_sessions_with_shifted_dates():
    state_json = (DATA_DIR / "state.json").read_text()
    assert not state_before((), date(2026, 11, 4), TODAY, state_json).questions

    previous = ("2026-11-04-1", "2026-11-04-2")
    state = state_before(previous, date(2026, 11, 5), TODAY, state_json)
    assert [q.session for q in state.questions] == ["2026-11-04-2"] * 3
    # Recorded the day before the session; shifted so that is the day before TODAY.
    assert {q.date for q in state.questions} == {"2026-11-30"}
    assert [(t.text[:3], t.closed is not None) for t in state.threads] == [
        ("Why", True),
        ("Ret", False),
    ]
    assert state.topics["derivative-definition"].status == "introduced"


# -- checks -----------------------------------------------------------------------------


def test_checks():
    log = [("record_question", {"topic": "limits"}), ("load_skill", {"name": "quiz"})]
    expect = {
        "calls": ["record_question", "load_skill:quiz", "load_skill:socratic"],
        "calls_any": [["get_outline", "read_section"]],
        "not_calls": ["end_session"],
        "mentions": {"reminder": r"third[- ]party"},
        "not_mentions": {"answer": r"6/5"},
    }
    results = {
        r.name: r.passed for r in run_checks(expect, "A THIRD-PARTY note; 6/5", log)
    }
    assert results == {
        "calls record_question": True,
        "calls load_skill:quiz": True,
        "calls load_skill:socratic": False,
        "calls one of get_outline, read_section": False,
        "does not call end_session": True,
        "mentions reminder": True,
        "does not mention answer": False,
    }
    with pytest.raises(ValueError, match="unknown expectations: typo"):
        run_checks({"typo": []}, "", [])


# -- replay -----------------------------------------------------------------------------


def test_replay_rebuilds_the_session_and_runs_one_turn(tmp_path):
    api = FakeAPI(
        response(
            tool_use(
                "t1",
                "record_question",
                {"topic": "limits", "question": "Q1", "outcome": "hinted"},
            ),
            stop="tool_use",
        ),
        response(text("Yes, approaches is the key word.")),
    )
    inp = replay_input(
        case(session="2026-11-04-2", turn=3, annotations=["My typed note"])
    )
    result = asyncio.run(
        replay(
            inp,
            sandbox=tmp_path / "box",
            stream=api,
            model="claude-sonnet-5-5",
            source_paths={},
            today=TODAY,
        )
    )
    assert result.reply == "Yes, approaches is the key word."
    assert result.tool_log == [
        ("record_question", {"topic": "limits", "question": "Q1", "outcome": "hinted"})
    ]

    first = api.requests[0]
    assert first["model"] == "claude-sonnet-5-5"
    roles = [m["role"] for m in first["messages"]]
    # learner, session-start context, tutor, learner, tutor, then the turn under test
    assert roles == [
        "user",
        "system",
        "assistant",
        "user",
        "assistant",
        "user",
        "system",
    ]
    assert "velocity session" in first["messages"][1]["content"]
    assert "Previous sessions: 2026-11-04-1" in first["messages"][1]["content"]
    assert first["messages"][2]["content"].startswith("Welcome back")
    assert first["messages"][5]["content"] == "Yes it is"
    assert "My typed note" in first["messages"][6]["content"]

    # The tool ran against the sandbox state, which started from the rebuilt state.
    states = list((tmp_path / "box/vault/Tutor").glob("*/state.json"))
    questions = json.loads(states[0].read_text())["questions"]
    assert [q["question"] for q in questions] == [
        "Q1"
    ]  # nothing recorded before session 2


def test_replay_on_a_free_model(tmp_path):
    """free: models run on the free engine's loop, with the history in OpenAI's shape."""
    from tests.test_free_engine import FakeServer, call, chunk, says

    server = FakeServer()
    server.replies += [
        [
            chunk(
                calls=[
                    call(
                        0,
                        "record_question",
                        '{"topic": "limits", "question": "Q1", "outcome": "partial"}',
                        "c1",
                    )
                ]
            )
        ],
        says("Nearly: approaches is the key word."),
    ]
    inp = replay_input(
        case(session="2026-11-04-2", turn=3, annotations=["My typed note"])
    )
    result = asyncio.run(
        replay(
            inp,
            sandbox=tmp_path / "box",
            stream=None,
            model="qwen3:4b",
            source_paths={},
            today=TODAY,
            free_create=server.create,
        )
    )
    assert result.reply == "Nearly: approaches is the key word."
    assert result.tool_log == [
        ("record_question", {"topic": "limits", "question": "Q1", "outcome": "partial"})
    ]
    assert result.usage.requests == 0  # nothing to pay for
    first = server.requests[0]
    assert first["model"] == "qwen3:4b"
    roles = [m["role"] for m in first["messages"]]
    # system prompt, then learner (with the session-start note), tutor, learner, tutor,
    # and the turn under test (with the annotation as a note)
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
    assert "Previous sessions: 2026-11-04-1" in first["messages"][1]["content"]
    assert first["messages"][-1]["content"].startswith("Yes it is")
    assert "My typed note" in first["messages"][-1]["content"]


def test_history_blocks_and_annotations_are_seeded(tmp_path):
    api = FakeAPI(response(text("ok")))
    inp = replay_input(case(session="2026-11-05-1", turn=5))
    asyncio.run(
        replay(
            inp,
            sandbox=tmp_path,
            stream=api,
            model="claude-sonnet-5-5",
            source_paths={"Stewart": tmp_path / "book.pdf"},
            today=TODAY,
        )
    )
    messages = api.requests[0]["messages"]
    systems = [m["content"] for m in messages if m["role"] == "system"]
    # Start context, then one annotation report per earlier annotated turn, then the pending one.
    assert systems[0].startswith("velocity session")
    assert sum("The learner added these annotations" in s for s in systems) == 3
    assert "a. 2" in systems[-1]
    # The earlier annotations are already known, so they aren't reported again.
    assert "That limit means" not in systems[-1]


def test_hand_written_case_with_notes(tmp_path):
    api = FakeAPI(response(text("ok")))
    inp = replay_input(
        case(
            subject={
                "name": "Biology",
                "slug": "biology",
                "notes": {"Bio/cells.md": "# Cells"},
            },
            history=[],
            message="/review-notes",
        )
    )
    asyncio.run(
        replay(
            inp,
            sandbox=tmp_path,
            stream=api,
            model="claude-sonnet-5-5",
            source_paths={},
            today=TODAY,
        )
    )
    context = api.requests[0]["messages"][1]["content"]
    assert "1 readable notes" in context and "Sources: none" in context
    assert "Previous sessions: none" in context


def test_missing_source_skips_the_case(tmp_path):
    inp = replay_input(case(session="2026-11-05-2", turn=1))
    with pytest.raises(SkipCase, match="Stewart"):
        asyncio.run(
            replay(
                inp,
                sandbox=tmp_path,
                stream=FakeAPI(),
                model="m",
                source_paths={},
                today=TODAY,
            )
        )


# -- runner -----------------------------------------------------------------------------


def test_run_all_checks_reports_and_respects_the_budget(tmp_path):
    cases = [
        case(
            id="a", session="2026-11-04-1", turn=1, expect={"mentions": {"hi": "hello"}}
        ),
        case(id="b", session="2026-11-05-2", turn=1),  # needs Stewart: skipped
        case(id="c", session="2026-11-04-1", turn=3),
    ]
    api = FakeAPI(response(text("hello there")), response(text("hi")))
    lines = []
    outcomes = asyncio.run(
        run_all(
            cases,
            ["claude-opus-5-5"],
            stream=api,
            sources={},
            sandbox=tmp_path,
            budget=0.001,  # the first case costs more than this
            concurrency=1,
            effort="medium",
            report=lines.append,
        )
    )
    assert [(o.case, o.status) for o in outcomes] == [
        ("a", "ok"),
        ("b", "skipped"),
        ("c", "skipped"),
    ]
    assert outcomes[0].passed and outcomes[0].cost == pytest.approx(
        (1000 * 4 + 100 * 20) / 1e6
    )
    assert outcomes[2].detail == "budget reached"
    assert lines[0].startswith("PASS  a [claude-opus-5-5] 1/1 checks")
    table = summary(outcomes, ["claude-opus-5-5"])
    assert "| claude-opus-5-5 | 1/1 | 1/1 | 0/0 | 2 | 0 | $0.01 | $0.00 |" in table


def test_failed_checks_are_listed():
    from evals.checks import CheckResult
    from evals.run import Outcome

    o = Outcome(
        "a",
        "m",
        "ok",
        [CheckResult("calls x", False, "no tools"), CheckResult("y", True)],
    )
    assert format_outcome(o).splitlines() == [
        "FAIL  a [m] 1/2 checks, $0.000",
        "        x calls x (no tools)",
    ]


def test_select_cases():
    cases = [case(id=i) for i in ["s1-a", "s1-b", "s2-a"]]
    assert [c.id for c in select_cases(cases, ["s1-*"], None)] == ["s1-a", "s1-b"]
    assert [c.id for c in select_cases(cases, None, 2)] == ["s1-a", "s1-b"]


def test_root_cause_unwraps_single_exception_groups():
    from evals.run import root_cause

    inner = ValueError("401")
    assert root_cause(ExceptionGroup("g", [ExceptionGroup("h", [inner])])) is inner
    group = ExceptionGroup("g", [ValueError(), KeyError()])
    assert root_cause(group) is group


def test_state_before_includes_this_sessions_earlier_changes():
    state_json = (DATA_DIR / "state.json").read_text()
    previous = tuple(f"2026-11-04-{n}" for n in range(1, 7))
    state = state_before(
        previous, date(2026, 11, 5), TODAY, state_json, ("2026-11-05-1", 1, 1)
    )
    assert [q.topic for q in state.questions][-1] == "limit-definition-intuitive"
    assert len(state.questions) == 4
    assert (
        state.threads[-1].text.startswith("Stewart §2.3 #15")
        and not state.threads[-1].closed
    )
    with pytest.raises(ValueError, match="fewer questions"):
        state_before(
            previous, date(2026, 11, 5), TODAY, state_json, ("2026-11-05-1", 3, 0)
        )


def test_case_session_state_reaches_the_replay_input():
    inp = replay_input(
        case(session="2026-11-05-1", turn=5, session_state={"questions": 1})
    )
    assert (inp.session_id, inp.session_questions, inp.session_threads) == (
        "2026-11-05-1",
        1,
        0,
    )


def test_citation_pattern_accepts_words_between_name_and_section():
    import re

    cite = next(c for c in load_cases() if c.id == "2026-11-05-1-t1").expect[
        "mentions"
    ]["a Stewart citation"]
    assert re.search(cite, "Stewart covers limits in Chapter 2: §2.2 (the idea)")
    assert re.search(cite, "see Stewart §2.4")
    assert not re.search(cite, "Stewart covers limits in Chapter 2.")


# -- judge ------------------------------------------------------------------------------


def verdicts(**overrides):
    from evals.judge import RUBRIC

    return {
        c: {"reason": f"{c} reason", "verdict": overrides.get(c, "pass")}
        for c in RUBRIC
    }


class FakeCreate:
    def __init__(self, *payloads, stop="end_turn"):
        self.payloads = list(payloads)
        self.stop = stop
        self.requests = []

    async def __call__(self, **params):
        self.requests.append(params)
        usage = NS(
            input_tokens=2000,
            output_tokens=500,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )
        return NS(
            content=[NS(type="thinking"), text(json.dumps(self.payloads.pop(0)))],
            stop_reason=self.stop,
            usage=usage,
        )


def test_judge_prompt_shows_what_the_tutor_saw():
    from evals.judge import judge_prompt

    c = case(session="2026-11-05-1", turn=5, judge="Fixes 'undefined' to DNE.")
    prompt = judge_prompt(
        c, replay_input(c), "All four are right.", [("record_question", {"topic": "x"})]
    )
    assert "Subject: Calculus. Sources: Stewart." in prompt
    assert "LEARNER: hi claude" in prompt
    assert "[The learner typed into the session log:] That limit means" in prompt
    assert "[Just before this, the learner typed into the session log:] a. 2" in prompt
    assert '- record_question({"topic": "x"})' in prompt
    assert "<tutor_reply>\nAll four are right.\n</tutor_reply>" in prompt
    assert "Case focus: Fixes 'undefined' to DNE." in prompt
    assert "- asks_before_telling (Asks before telling):" in prompt


def test_judge_requests_structured_verdicts():
    from evals.judge import judge

    create = FakeCreate(verdicts(correct="fail"))
    c = case(session="2026-11-04-1", turn=1)
    result = asyncio.run(judge(create, c, replay_input(c), "Hi", [], "claude-opus-5-5"))
    assert result.verdicts["correct"] == {"reason": "correct reason", "verdict": "fail"}
    assert result.usage.cost("claude-opus-5-5") == pytest.approx(
        (2000 * 4 + 500 * 20) / 1e6
    )
    params = create.requests[0]
    schema = params["output_config"]["format"]["schema"]
    assert schema["required"][0] == "asks_before_telling"
    assert schema["properties"]["focus"]["properties"]["verdict"]["enum"] == [
        "pass",
        "fail",
        "n/a",
    ]
    assert params["fallbacks"] == "default"

    with pytest.raises(RuntimeError, match="declined"):
        asyncio.run(
            judge(FakeCreate({}, stop="refusal"), c, replay_input(c), "Hi", [], "m")
        )


def test_run_all_with_the_judge(tmp_path):
    from evals.run import judge_table

    cases = [case(id="a", session="2026-11-04-1", turn=1)]
    create = FakeCreate(verdicts(asks_before_telling="fail", focus="n/a"))
    lines = []
    outcomes = asyncio.run(
        run_all(
            cases,
            ["claude-sonnet-5-5"],
            stream=FakeAPI(response(text("hello"))),
            sources={},
            sandbox=tmp_path,
            budget=5,
            concurrency=1,
            effort="medium",
            create=create,
            judge_model="claude-opus-5-5",
            report=lines.append,
        )
    )
    o = outcomes[0]
    assert o.verdicts("fail") == ["asks_before_telling"] and o.judge_cost > 0
    assert "judge 4 pass, 1 fail" in lines[0]
    assert "judge fail asks_before_telling: asks_before_telling reason" in lines[0]
    table = judge_table(outcomes, ["claude-sonnet-5-5"])
    assert "| Asks before telling | 0/1 |" in table
    assert "| Case focus | 0/0 |" in table


def test_judge_errors_are_reported_without_losing_the_case(tmp_path):
    cases = [case(id="a", session="2026-11-04-1", turn=1)]
    outcomes = asyncio.run(
        run_all(
            cases,
            ["claude-sonnet-5-5"],
            stream=FakeAPI(response(text("hello"))),
            sources={},
            sandbox=tmp_path,
            budget=5,
            concurrency=1,
            effort="medium",
            create=FakeCreate({}, stop="refusal"),
            judge_model="claude-opus-5-5",
            report=lambda _: None,
        )
    )
    assert outcomes[0].status == "ok" and outcomes[0].reply == "hello"
    assert "declined" in outcomes[0].judge_error


# -- labels -----------------------------------------------------------------------------


def judged(case_id, model, **overrides):
    return {
        "case": case_id,
        "model": model,
        "status": "ok",
        "reply": "Reply with $x^2$.\n\nSecond line.",
        "tool_calls": [["record_question", {"topic": "limits"}]],
        "judge": verdicts(**overrides),
    }


def test_label_export_parse_and_agreement(tmp_path):
    from evals.label import agreement, export, parse_labels

    run = tmp_path / "run.json"
    outcomes = [
        judged("2026-11-05-1-t5", "opus", correct="fail"),
        judged("2026-11-05-1-t5", "sonnet"),
        judged("hand-own-error", "sonnet"),
        {"case": "2026-11-04-1-t1", "model": "opus", "status": "error"},
    ]
    run.write_text(json.dumps({"outcomes": outcomes}))
    path = export(run, 2, seed=1, out=tmp_path / "labels.md")
    text = path.read_text()
    assert text.count("## Reply") == 2 and "## Reply 1 of 2" in text
    assert "> [!note]- Conversation so far" in text
    assert "> Reply with $x^2$." in text and "`record_question(topic='limits')`" in text
    assert "judge" not in text.split("**Criteria:**")[1].lower().split("---")[1]
    assert '"model":' in text  # only in the hidden item comments
    assert "opus" not in text.replace('"model": "opus"', "")

    # Fill in: agree everywhere except "correct", which is labeled pass for every item.
    filled = text.replace("(correct): _", "(correct): pass").replace(
        "(focus): _", "(focus): N/A"
    )
    filled = re.sub(r"\((\w+)\): _", r"(\1): pass", filled)
    run_path, items = parse_labels(filled)
    assert run_path == run.as_posix() and len(items) == 2
    assert all(len(i.labels) == 6 for i in items)
    assert items[0].labels["focus"] == "n/a"

    report = agreement(items, outcomes)
    assert "| Asks before telling | 2 | 2 | 100% | 1.00 |" in report
    for model in {i.model for i in items}:
        assert f"| All, {model} | 6 |" in report
    picked = {(i.case, i.model) for i in items}
    if ("2026-11-05-1-t5", "opus") in picked:
        assert "you pass, judge fail (correct reason)" in report


def test_unfilled_labels_are_left_out():
    from evals.label import parse_labels

    text = (
        '<!-- item {"case": "a", "model": "m"} -->\n**Your labels** (pass, fail, or n/a):\n'
        "- Correct (correct): fail\n- Case focus (focus): _\n- Notes (optional): ok\n"
    )
    _, items = parse_labels(text)
    assert items[0].labels == {"correct": "fail"}


def test_cohen_kappa():
    from evals.label import cohen_kappa

    assert cohen_kappa([]) is None
    assert cohen_kappa([("pass", "pass"), ("fail", "fail")]) == 1.0
    assert cohen_kappa([("pass", "pass"), ("pass", "pass")]) == 1.0
    assert cohen_kappa([("pass", "fail"), ("fail", "pass")]) == pytest.approx(-1.0)


def test_records_with_tool_calls_replay_them(tmp_path):
    import shutil

    data = tmp_path / "evaldata"
    records = data / "records" / "calculus"
    records.mkdir(parents=True)
    shutil.copy(DATA_DIR / "state.json", data / "state.json")
    question = {"topic": "t", "question": "2+2", "outcome": "correct"}
    events = [
        ("event", "start", "Calculus", {"sources": []}),
        ("learner", None, "Quiz me", None),
        ("event", "tool", "load_skill", {"input": {"name": "quiz"}}),
        ("tutor", None, "Q1: what is 2+2?", None),
        ("learner", None, "4", None),
        ("event", "tool", "record_question", {"input": question}),
        ("tutor", None, "Right. Q2: 3+3?", None),
        ("learner", None, "6", None),
    ]
    lines = [
        json.dumps(
            {"ts": f"2026-11-07T10:00:0{i}", "role": role, "text": text, "title": None}
            | {"kind": kind, "meta": meta}
        )
        for i, (role, kind, text, meta) in enumerate(events)
    ]
    (records / "2026-11-07-1.jsonl").write_text("\n".join(lines))

    inp = replay_input(case(session="2026-11-07-1", turn=3), data_dir=data)
    tool_turns = [t.text for t in inp.history if t.role == "tool"]
    assert tool_turns == ["load_skill", "record_question"]

    api = FakeAPI(response(text("ok")))
    box = tmp_path / "box"
    asyncio.run(
        replay(
            inp,
            sandbox=box,
            stream=api,
            model="m",
            source_paths={},
            today=TODAY,
            data_dir=data,
        )
    )
    messages = api.requests[0]["messages"]
    blocks = [m["content"][0] for m in messages if isinstance(m["content"], list)]
    assert [(b["type"], b.get("name")) for b in blocks] == [
        ("tool_use", "load_skill"),
        ("tool_result", None),
        ("tool_use", "record_question"),
        ("tool_result", None),
    ]
    assert blocks[2]["input"] == question
    assert blocks[1]["tool_use_id"] == blocks[0]["id"]
    # The recorded record_question ran again, so the sandbox state has the question.
    state = next((box / "vault/Tutor").glob("*/state.json"))
    assert [q["question"] for q in json.loads(state.read_text())["questions"]] == [
        "2+2"
    ]

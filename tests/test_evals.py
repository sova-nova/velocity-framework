import asyncio
import json
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
    assert "| claude-opus-5-5 | 1/1 | 1/1 | 2 | 0 | $0.01 |" in table


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

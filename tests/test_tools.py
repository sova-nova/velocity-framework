import asyncio
from pathlib import Path

import pymupdf
import pytest
from mcp import Client

from velocity.config import Config, SourceRef, Subject
from velocity.mcp_server import TOOL_NAMES, build_server
from velocity.session import Session, record_dir, set_active
from velocity.tools import ToolError, TutorTools

PAGES = {
    1: "## 1.1 Limits\n\nA limit is...\n\n## 1.1 EXERCISES\n\n**1.** Find the limit.",
    2: "## 1.2 Continuity\n\nContinuous means...",
}


def fake_ocr(pdf_path, first_page, last_page, image_dir):
    return {p: PAGES[p] for p in range(first_page, last_page + 1)}


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def book(tmp_path) -> Path:
    path = tmp_path / "book.pdf"
    doc = pymupdf.open()
    for _ in PAGES:
        doc.new_page()
    doc.set_toc(
        [[1, "Ch 1: Basics", 1], [2, "1.1: Limits", 1], [2, "1.2: Continuity", 2]]
    )
    doc.save(path)
    return path


@pytest.fixture
def subject(tmp_path, book) -> Subject:
    vault = tmp_path / "vault"
    return Subject(
        "calculus",
        "Calculus",
        vault / "Tutor/calculus",
        "",
        (),
        (SourceRef(book, "Book"), SourceRef(tmp_path / "missing.pdf", "Gone")),
    )


@pytest.fixture
def tools(tmp_path, subject) -> TutorTools:
    return TutorTools(Config(tmp_path / "vault"), subject, ocr_factory=lambda: fake_ocr)


def start_session(subject: Subject) -> Session:
    s = Session.start(
        subject.slug,
        subject.name,
        record_dir(subject.slug),
        subject.sessions_dir,
        ["Book"],
    )
    set_active(subject.slug, s.id)
    return s


def test_sources_outline_and_sections(tools):
    assert tools.list_sources() == "- Book (book.pdf)\n- Gone (missing.pdf)"
    outline = tools.get_outline("Book")
    assert "  1.1: Limits [id: 1.1, PDF pp. 1-2]" in outline
    text = tools.read_section("Book", "1.1")
    assert text.startswith("Book §1.1: 1.1: Limits (PDF pp. 1-2)\n\n## 1.1 Limits")
    assert "Continuity" not in text


def test_no_sources_message(tmp_path, subject):
    bare = Subject("x", "X", subject.dir, "", (), ())
    assert "no sources" in TutorTools(Config(tmp_path), bare).list_sources()


@pytest.mark.parametrize(
    "call, message",
    [
        (
            lambda t: t.get_outline("Nope"),
            "Unknown source 'Nope'. Available: Book, Gone",
        ),
        (lambda t: t.get_outline("Gone"), "not found"),
        (lambda t: t.read_section("Book", "9.9"), "No section '9.9'"),
        (lambda t: t.append_log("x", "y"), "No active session"),
    ],
)
def test_expected_errors(tools, call, message):
    with pytest.raises(ToolError, match=message):
        call(tools)


def test_append_log_writes_to_active_session(tools, subject):
    s = start_session(subject)
    tools.append_log("Problem 1", "Prove it.", kind="example")
    s.render()
    assert "> [!example] Problem 1\n> Prove it." in s.log_path.read_text()


def test_sessions_and_log_check(tools, subject):
    s = start_session(subject)
    s.append("learner", "hello")
    s.render()
    assert tools.list_sessions() == f"- {s.id}: untitled; starts: 'hello' (current)"
    assert tools.set_session_title("  Limits:\n the basics ") == (
        "Session title set: Limits: the basics"
    )
    assert tools.list_sessions() == f"- {s.id}: Limits: the basics (current)"
    with pytest.raises(ToolError, match="empty"):
        tools.set_session_title("   ")
    assert "**You:** hello" in tools.read_session(s.id)
    assert tools.check_log() == "The log matches the record; no annotations."

    s.log_path.write_text(
        s.log_path.read_text().replace("**You:** hello", "") + "my note\n"
    )
    report = tools.check_log()
    assert "- my note" in report and "- **You:** hello" in report
    tools.restore_log()
    assert "**You:** hello" in s.log_path.read_text()


def test_mcp_server_exposes_tools_and_reports_expected_errors(tools, subject):
    async def run():
        async with Client(build_server(tools)) as client:
            listed = await client.list_tools()
            outline = await client.call_tool("get_outline", {"source": "Book"})
            bad = await client.call_tool("get_outline", {"source": "Nope"})
            return listed, outline, bad

    listed, outline, bad = asyncio.run(run())
    assert sorted(t.name for t in listed.tools) == sorted(TOOL_NAMES)
    assert (
        not outline.is_error and "1.2: Continuity [id: 1.2" in outline.content[0].text
    )
    assert bad.is_error and "Unknown source 'Nope'" in bad.content[0].text


def test_learner_state_tools(tmp_path, subject):
    from datetime import date

    tools = TutorTools(
        Config(tmp_path / "vault"), subject, today=lambda: date(2026, 11, 4)
    )
    start_session(subject)
    assert "next review 2026-11-05" in tools.record_question(
        "Limits", "What is lim x->2 of x^2?", "correct", source="Book §1.1"
    )
    assert (
        tools.set_topic_status("Continuity", "introduced")
        == "Continuity is now introduced."
    )
    assert tools.open_thread("Why epsilon first?") == "Opened thread #1."
    state = tools.read_state()
    assert "Limits: practicing" in state and "source Book §1.1" in state
    assert "#1 Why epsilon first?" in state
    assert (subject.dir / "progress.md").exists()
    assert tools.close_thread(1) == "Closed thread #1."
    with pytest.raises(ToolError, match="outcome"):
        tools.record_question("Limits", "q", "great")


def test_state_tools_over_mcp(tools, subject):
    async def run():
        async with Client(build_server(tools)) as client:
            ok = await client.call_tool(
                "record_question",
                {"topic": "Limits", "question": "q", "outcome": "hinted"},
            )
            bad = await client.call_tool(
                "record_question",
                {"topic": "Limits", "question": "q", "outcome": "great"},
            )
            return ok, bad

    ok, bad = asyncio.run(run())
    assert not ok.is_error and "Recorded (hinted)" in ok.content[0].text
    assert bad.is_error


def test_get_exercises(tools):
    text = tools.get_exercises("Book", "1.1")
    assert text.startswith("Exercises from Book §1.1: 1.1: Limits")
    assert text.endswith("## 1.1 EXERCISES\n\n**1.** Find the limit.")
    assert "A limit is" not in text
    with pytest.raises(ToolError, match="No exercises heading"):
        tools.get_exercises("Book", "1.2")


def test_a_recorded_answer_is_a_line_in_the_log(tools, subject):
    """The web page draws it as a divider colored by the outcome (decided 2026-11-10)."""
    from velocity.chat import conversation

    s = start_session(subject)
    s.append("learner", "It is 4.")
    tools.record_question(
        "Limits by factoring", "$\\lim_{x\\to2}\\frac{x^2-4}{x-2}$", "correct"
    )
    tools.record_question("limits by factoring", "Why cancel?", "hinted")
    s.append("tutor", "> [!success]\n> Right.")
    s.render()
    log = s.log_path.read_text(encoding="utf-8")
    assert "*✓ Recorded: correct · Limits by factoring*" in log
    assert (
        "*~ Recorded: hinted · Limits by factoring*" in log
    )  # the topic's stored name
    assert log.index("It is 4.") < log.index("✓ Recorded") < log.index("[!success]")
    assert "**Tutor:**\n\n> [!success]\n> Right." in log  # a callout on its own line
    assert s.check_log().missing == []
    assert [m for m in conversation(s) if m["role"] == "recorded"] == [
        {"role": "recorded", "text": "correct", "title": "Limits by factoring"},
        {"role": "recorded", "text": "hinted", "title": "Limits by factoring"},
    ]
    assert not s.empty


def test_recording_without_a_session_still_records_the_answer(tools, subject):
    assert tools.record_question("Limits", "Q?", "incorrect").startswith("Recorded")

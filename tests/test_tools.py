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
    1: "## 1.1 Limits\n\nA limit is...",
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
    assert tools.list_sessions() == f"- {s.id} (current)"
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

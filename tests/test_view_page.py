"""view_page: PDF pages as images for the tutor (tools, MCP layer, own loop)."""

import asyncio
import base64

import pymupdf
import pytest
from mcp import Client

from velocity.agent import mcp_tool_caller
from velocity.config import Config, SourceRef, Subject
from velocity.mcp_server import build_server
from velocity.pdf_source import PAGE_IMAGE_EDGE
from velocity.tools import PngImage, ToolError, TutorTools

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    book = tmp_path / "book.pdf"
    doc = pymupdf.open()
    for n in range(1, 6):
        doc.new_page().insert_text((72, 72), f"Page {n}")
    doc.set_toc([[1, "Ch 1: Limits", 1], [2, "1.1: The Squeeze Theorem", 3]])
    doc.save(book)
    notes = tmp_path / "notes.md"
    notes.write_text("# Limits\n\nNear, not at.\n")
    subject = Subject(
        "calculus",
        "Calculus",
        tmp_path / "vault/Tutor/calculus",
        "",
        (),
        (SourceRef(book, "Book"), SourceRef(notes, "Notes")),
    )
    return TutorTools(Config(tmp_path / "vault"), subject)


def test_view_page_returns_a_header_and_page_images(tools):
    header, image = tools.view_page("Book", 3)
    assert header.startswith("Book PDF p. 3 (Book §1.1: 1.1: The Squeeze Theorem)")
    assert isinstance(image, PngImage) and image.data.startswith(PNG)
    pixels = pymupdf.Pixmap(image.data)
    assert max(pixels.width, pixels.height) == PAGE_IMAGE_EDGE


def test_view_page_shows_at_most_three_pages_and_stops_at_the_end(tools):
    result = tools.view_page("Book", 1, count=10)
    assert result[0].startswith("Book PDF pp. 1-3") and len(result) == 4
    assert len(tools.view_page("Book", 5, count=3)) == 2  # only page 5 is left


def test_view_page_errors(tools):
    with pytest.raises(ToolError, match="pages 1-5; there is no page 6"):
        tools.view_page("Book", 6)
    with pytest.raises(ToolError, match="isn't a PDF"):
        tools.view_page("Notes", 1)
    with pytest.raises(ToolError, match="Unknown source"):
        tools.view_page("Nope", 1)


def test_view_page_over_mcp_and_in_the_own_loop(tools):
    async def run():
        async with Client(build_server(tools)) as mcp:
            raw = await mcp.call_tool("view_page", {"source": "Book", "page": 2})
            content, is_error = await mcp_tool_caller(mcp)(
                "view_page", {"source": "Book", "page": 2, "count": 2}
            )
            text, _ = await mcp_tool_caller(mcp)("list_sources", {})
            return raw, content, is_error, text

    raw, content, is_error, text = asyncio.run(run())
    assert [c.type for c in raw.content] == ["text", "image"]
    assert raw.content[1].mime_type == "image/png"
    # The own loop sends images as API image blocks inside the tool_result.
    assert not is_error
    assert [b["type"] for b in content] == ["text", "image", "image"]
    source = content[1]["source"]
    assert source["type"] == "base64" and source["media_type"] == "image/png"
    assert base64.b64decode(source["data"]).startswith(PNG)
    assert isinstance(text, str)  # text-only results stay plain text

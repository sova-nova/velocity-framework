import os
from pathlib import Path

import pymupdf
import pytest

from velocity.cli import main
from velocity.config import (
    ConfigError,
    add_source,
    create_subject,
    load_config,
    load_subject,
)


def make_pdf(path: Path, toc: bool) -> Path:
    doc = pymupdf.open()
    doc.new_page()
    if toc:
        doc.set_toc([[1, "Ch 1: Basics", 1]])
    doc.save(path)
    return path


@pytest.fixture
def config(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (tmp_path / "cfg/velocity").mkdir(parents=True)
    (tmp_path / "cfg/velocity/config.toml").write_text(
        f"vault_path = '{vault}'\nllama_cpp_binary = \"~/llama/llama-server\"\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfg = load_config()
    create_subject(cfg, "Calculus")
    return cfg


def test_llama_path_from_config(config):
    assert config.llama_cpp_binary == Path("~/llama/llama-server").expanduser()


def test_add_source_appends_and_keeps_comments(config, tmp_path):
    pdf = make_pdf(tmp_path / 'Stewart "8th".pdf', toc=True)
    added = add_source(config, "calculus", pdf, "Stewart")
    assert (added.name, added.path) == ("Stewart", pdf.resolve())
    text = (config.tutor_dir / "calculus/subject.toml").read_text()
    assert "# Optional sources to ground teaching in" in text  # comments kept
    assert [s.name for s in load_subject(config, "calculus").sources] == ["Stewart"]
    add_source(config, "calculus", make_pdf(tmp_path / "spivak.pdf", toc=True))
    assert [s.name for s in load_subject(config, "calculus").sources] == [
        "Stewart",
        "spivak",
    ]


@pytest.mark.parametrize(
    "setup, message",
    [
        (lambda d: d / "missing.pdf", "not a file"),
        (
            lambda d: (d / "notes.txt").write_text("x") and d / "notes.txt",
            "unsupported file type",
        ),
    ],
)
def test_add_source_rejects_bad_files(config, tmp_path, setup, message):
    with pytest.raises(ConfigError, match=message):
        add_source(config, "calculus", setup(tmp_path))


def test_add_source_rejects_duplicates(config, tmp_path):
    pdf = make_pdf(tmp_path / "book.pdf", toc=True)
    add_source(config, "calculus", pdf, "Book")
    with pytest.raises(ConfigError, match="already has a source named"):
        add_source(
            config, "calculus", make_pdf(tmp_path / "other.pdf", toc=True), "Book"
        )
    with pytest.raises(ConfigError, match="already a source"):
        add_source(config, "calculus", pdf, "Again")


def test_cli_source_add_and_list(config, tmp_path, capsys):
    assert (
        main(
            [
                "source",
                "add",
                "calculus",
                str(make_pdf(tmp_path / "a.pdf", toc=True)),
                "--name",
                "A",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert (
        "Added 'A' to calculus" in out and "Found 1 entries" in out and "Tip" not in out
    )

    assert (
        main(
            [
                "source",
                "add",
                "calculus",
                str(make_pdf(tmp_path / "scan.pdf", toc=False)),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "no table of contents" in out and 'cites it as "scan' in out

    assert main(["source", "list", "calculus"]) == 0
    assert capsys.readouterr().out.splitlines()[0].startswith("A: ")

    assert main(["source", "add", "calculus", str(tmp_path / "nope.pdf")]) == 1
    assert "not a file" in capsys.readouterr().err


def test_ocr_uses_configured_llama_binary(config, monkeypatch):
    from velocity.tools import ToolError, default_ocr

    monkeypatch.delenv("LLAMA_CPP_BINARY", raising=False)
    try:
        default_ocr(config)
    except ToolError:
        pass  # OCR packages not installed (as in CI); the variable is set before importing
    assert os.environ.pop("LLAMA_CPP_BINARY") == config.llama_cpp_binary.as_posix()


def test_cli_source_recheck(config, tmp_path, monkeypatch, capsys):
    from velocity.ocr import OcrPage
    from velocity.pdf_source import default_cache_dir

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    pdf = make_pdf(tmp_path / "book.pdf", toc=True)
    add_source(config, "calculus", pdf, "Book")
    assert main(["source", "recheck", "calculus"]) == 0
    assert "Book: all cached pages are checked." in capsys.readouterr().out

    pages = default_cache_dir(pdf) / "pages"
    pages.mkdir(parents=True)
    (pages / "1.md").write_text("old")

    def fake_ocr(pdf_path, first, last, image_dir):
        return {1: OcrPage("new", [])}

    monkeypatch.setattr("velocity.tools.default_ocr", lambda cfg: fake_ocr)
    assert main(["source", "recheck", "calculus"]) == 0
    out = capsys.readouterr().out
    assert (
        "OCR'ing 1 prepared page again" in out and "0 dropped passages restored." in out
    )
    assert (pages / "1.md").read_text() == "new"
    assert (pages / "1.check.json").read_text() == "[]"


def test_cli_prepare_a_file_in_no_subject(config, tmp_path, monkeypatch, capsys):
    from velocity.ocr import OcrPage
    from velocity.pdf_source import default_cache_dir

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    pdf = make_pdf(tmp_path / "book.pdf", toc=True)
    monkeypatch.setattr(
        "velocity.tools.default_ocr",
        lambda cfg: lambda pdf_path, first, last, image_dir: {1: OcrPage("one", [])},
    )
    assert main(["source", "prepare", "--file", str(pdf), "--all"]) == 0
    assert "book.pdf: done, 1 pages OCR'd." in capsys.readouterr().out
    assert (default_cache_dir(pdf) / "pages/1.md").read_text() == "one"

    # A subject that adds it later finds the pages prepared.
    add_source(config, "calculus", pdf, "Book")
    assert main(["source", "prepare", "calculus", "--all"]) == 0
    assert "Book: the whole book is already prepared." in capsys.readouterr().out

    # Exactly one of a subject or --file.
    assert main(["source", "prepare", "--all"]) == 1
    assert main(["source", "prepare", "calculus", "--file", str(pdf), "--all"]) == 1
    assert "Name a subject, or a PDF with --file" in capsys.readouterr().out
    notes = tmp_path / "notes.md"
    notes.write_text("# x\n")
    assert main(["source", "prepare", "--file", str(notes), "--all"]) == 1
    assert "not a PDF file" in capsys.readouterr().out

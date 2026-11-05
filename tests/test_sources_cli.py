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
        f'vault_path = "{vault}"\nllama_cpp_binary = "~/llama/llama-server"\n'
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
        (lambda d: (d / "notes.txt").write_text("x") and d / "notes.txt", "not a PDF"),
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
    from velocity.tools import ToolError, _default_ocr

    monkeypatch.delenv("LLAMA_CPP_BINARY", raising=False)
    try:
        _default_ocr(config)
    except ToolError:
        pass  # OCR packages not installed (as in CI); the variable is set before importing
    assert os.environ.pop("LLAMA_CPP_BINARY") == config.llama_cpp_binary.as_posix()

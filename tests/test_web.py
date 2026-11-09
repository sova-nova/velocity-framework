from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from velocity.config import Config, create_subject, load_subject
from velocity.session import ENDED_WITH_WRAP_UP, Session, record_dir
from velocity.web import build_app

T0 = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.fixture
def config(tmp_path) -> Config:
    (tmp_path / "vault").mkdir()
    config = Config((tmp_path / "vault").resolve())
    create_subject(config, "Calculus")
    return config


@pytest.fixture
def client(config) -> TestClient:
    return TestClient(build_app(config), base_url="http://127.0.0.1:8765")


def session(config, day=0, learner=("What is a limit?",), end=None) -> Session:
    subject = load_subject(config, "calculus")
    now = T0 + timedelta(days=day)
    s = Session.start(
        "calculus",
        "Calculus",
        record_dir("calculus"),
        subject.sessions_dir,
        sources=[],
        now=now,
    )
    for i, text in enumerate(learner):
        s.append("learner", text, now=now + timedelta(seconds=2 * i + 1))
        s.append(
            "tutor", "What is $f(x)$ near $a$?", now=now + timedelta(seconds=2 * i + 2)
        )
    s.render()
    if end:
        s.end(end, now=now + timedelta(minutes=5))
    return s


def test_page_and_static_files(client):
    page = client.get("/")
    assert page.status_code == 200 and "<title>velocity</title>" in page.text
    assert "katex@0.16.11" in page.text  # pinned CDN versions
    assert client.get("/static/app.js").status_code == 200
    reader = client.get("/static/reader.js")
    assert reader.status_code == 200 and "pdfjs-dist@6.4.299" in reader.text
    assert client.get("/static/style.css").status_code == 200


def test_subjects(client):
    assert client.get("/api/subjects").json() == [
        {"slug": "calculus", "name": "Calculus", "sources": []}
    ]


def test_session_list_newest_first(client, config):
    session(config, day=0, end=ENDED_WITH_WRAP_UP)
    session(config, day=1, learner=("one", "two"))
    listing = client.get("/api/subjects/calculus/sessions").json()
    assert [(s["id"], s["turns"], s["ended"]) for s in listing] == [
        ("2026-11-05-1", 2, None),
        ("2026-11-04-1", 1, "wrapped-up"),
    ]
    assert listing[1]["first_message"] == "What is a limit?"
    assert client.get("/api/subjects/nope/sessions").status_code == 404


def test_session_log_shows_the_markdown_with_learner_edits(client, config):
    s = session(config, end="no-wrap-up")
    s.log_path.write_text(s.log_path.read_text() + "\nMy own note.\n")
    log = client.get(f"/api/subjects/calculus/sessions/{s.id}").json()
    assert log["from_log"] is True
    assert log["markdown"].startswith("# Calculus")  # front matter removed
    assert "**You:** What is a limit?" in log["markdown"]
    assert "My own note." in log["markdown"]


def test_session_log_falls_back_to_the_record(client, config):
    s = session(config)
    s.log_path.unlink()
    log = client.get(f"/api/subjects/calculus/sessions/{s.id}").json()
    assert log["from_log"] is False
    assert "**Tutor:** What is $f(x)$ near $a$?" in log["markdown"]
    assert not s.log_path.exists()  # nothing written


@pytest.mark.parametrize("session_id", ["2026-11-09-1", "..%2F..%2Fsecrets", "x"])
def test_unknown_or_malformed_sessions_are_404(client, config, session_id):
    session(config)
    response = client.get(f"/api/subjects/calculus/sessions/{session_id}")
    assert response.status_code == 404


def test_other_hosts_are_rejected(config):
    # DNS rebinding: a page on another domain that resolves to 127.0.0.1.
    client = TestClient(build_app(config), base_url="http://evil.example:8765")
    assert client.get("/api/subjects").status_code == 400


def test_cross_origin_changes_are_rejected(client):
    response = client.post("/api/subjects", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403
    same = client.post("/api/subjects", headers={"Origin": "http://127.0.0.1:8765"})
    assert same.status_code == 405  # passes the check; there is no POST route yet


def test_cli_web_starts_uvicorn_on_localhost(config, monkeypatch, capsys):
    from velocity import cli

    calls = {}
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(
        "uvicorn.run", lambda app, **kwargs: calls.update(kwargs, app=app)
    )
    assert cli.main(["web", "calculus", "--no-browser", "--port", "9000"]) == 0
    assert calls["host"] == "127.0.0.1" and calls["port"] == 9000
    assert "http://127.0.0.1:9000/?subject=calculus" in capsys.readouterr().out


# -- the source reader (M9) --------------------------------------------------------------


@pytest.fixture
def with_sources(tmp_path, config):
    import pymupdf

    from velocity.config import add_source

    book = tmp_path / "book.pdf"
    doc = pymupdf.open()
    for text in ["Intro", "Limits", "Continuity"]:
        doc.new_page().insert_text((72, 72), text)
    doc.set_toc(
        [[1, "Ch 1: Limits", 1], [2, "1.1: Limits", 2], [2, "1.2: Continuity", 3]]
    )
    doc.save(book)
    notes = tmp_path / "notes.md"
    notes.write_text("# Limits\n\nNear, not at.\n\n# Continuity\n\nNo gaps.\n")
    add_source(config, "calculus", book, "Book")
    add_source(config, "calculus", notes, "Notes")
    return book


def test_reader_lists_sources_with_outlines(client, with_sources):
    sources = {
        s["name"]: s for s in client.get("/api/subjects/calculus/sources").json()
    }
    assert sources["Book"]["kind"] == "pdf"
    assert [(e["id"], e["start_page"]) for e in sources["Book"]["outline"]] == [
        ("ch1", 1),
        ("1.1", 2),
        ("1.2", 3),
    ]
    assert sources["Notes"]["kind"] == "text"
    assert [e["id"] for e in sources["Notes"]["outline"]] == ["limits", "continuity"]


def test_reader_sends_pdfs_in_ranges_and_text_sections(client, with_sources):
    base = "/api/subjects/calculus/sources"
    whole = client.get(f"{base}/Book/file")
    assert whole.status_code == 200
    assert whole.headers["content-type"] == "application/pdf"
    assert whole.content == with_sources.read_bytes()
    part = client.get(f"{base}/Book/file", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100

    section = client.get(f"{base}/Notes/sections/continuity").json()
    assert section["title"] == "Continuity" and "No gaps." in section["markdown"]
    assert client.get(f"{base}/Notes/sections/nope").status_code == 404
    assert client.get(f"{base}/Notes/file").status_code == 400  # text: no file
    assert client.get(f"{base}/Book/sections/1.1").status_code == 400  # PDF: pages


def test_reader_only_sends_files_listed_as_sources(client, with_sources, tmp_path):
    base = "/api/subjects/calculus/sources"
    (tmp_path / "secret.pdf").write_bytes(b"%PDF-1.4 PRIVATE-CONTENTS")
    for name in [
        "secret",
        "secret.pdf",
        "..%2Fsecret.pdf",
        "Book%2F..%2F..%2Fsecret.pdf",
    ]:
        response = client.get(f"{base}/{name}/file")
        assert response.status_code == 404, name
        assert b"PRIVATE-CONTENTS" not in response.content
    assert client.get("/api/subjects/nope/sources").status_code == 404

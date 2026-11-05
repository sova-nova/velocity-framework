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

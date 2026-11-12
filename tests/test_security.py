"""App-wide security rules (security review, 2026-11-12), checked over every route the
server has, so a route added later is covered without anyone remembering to add it here.

- Requests that change something must come from velocity's own page (its Origin).
- Requests must name the local server as their host (against DNS rebinding).
- Reading (GET) changes no file.
- Every response carries the security headers (the Content-Security-Policy).
- Keys never reach the page.
- Files are served only from where they belong (no path tricks).

The fixes these guard are in DESIGN.md's decisions log; each fix has its own tests too.
"""

import os
import re

import pymupdf
import pytest
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from velocity import keys, ollama
from velocity.config import Config, add_source, create_subject
from velocity.library import library
from velocity.session import Session, record_dir
from velocity.web import CONTENT_SECURITY_POLICY, build_app, serve_app

BASE = "http://127.0.0.1:8765"
SECRET = "sk-ant-security-test-0123456789"
FREE_SECRET = "or-security-test-9876543210"
CHANGES = {"POST", "PUT", "PATCH", "DELETE"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A vault with a subject, a PDF book and a session; a saved key and one in the
    environment; Ollama answering without the network."""
    vault = tmp_path / "vault"
    vault.mkdir()
    config = Config(vault.resolve(), openai_base_url="https://openrouter.ai/api/v1")
    create_subject(config, "Calculus")
    pdf = tmp_path / "book.pdf"
    doc = pymupdf.open()
    for i in range(2):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}")
    doc.set_toc([[1, "Chapter 1", 1]])
    doc.save(pdf)
    add_source(config, "calculus", pdf, "Book")
    subject_dir = vault / "Tutor" / "calculus"
    session = Session.start(
        "calculus", "Calculus", record_dir("calculus"), subject_dir / "sessions", []
    )
    session.append("learner", "What is a limit?")
    session.append("tutor", "What is $f(x)$ near $a$?")
    session.render()
    session.end("wrap-up")

    keys.save_key(keys.ANTHROPIC, SECRET)
    keys.save_key(keys.free_key_name(config.openai_base_url), FREE_SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)

    async def no_network():
        return {"running": False, "installed": False, "models": []}

    monkeypatch.setattr(ollama, "find", no_network)
    app = build_app(config)
    return {
        "config": config,
        "app": app,
        "session": session.id,
        "entry": library(config)[0].id,
        "roots": [tmp_path, *_xdg_roots()],
    }


def _xdg_roots():
    return [os.environ["XDG_DATA_HOME"], os.environ["XDG_CONFIG_HOME"]]


def routes(world) -> list[tuple[str, str]]:
    """(method, path) for every route of the app, with its path parameters filled in from
    the world. A parameter this doesn't know fails the test: add it here."""
    values = {
        "slug": "calculus",
        "name": "Book",
        "session_id": world["session"],
        "section_id": "1",
        "entry": world["entry"],
    }
    out = []
    for route in world["app"].routes:
        if isinstance(route, Mount):
            continue  # /static: checked on its own below
        assert isinstance(route, Route), route
        params = re.findall(r"{(\w+)}", route.path)
        unknown = [p for p in params if p not in values]
        assert not unknown, f"{route.path}: add {unknown} to the test's values"
        path = route.path
        for p in params:
            path = path.replace("{" + p + "}", values[p])
        for method in sorted(route.methods or {"GET"}):
            if method != "HEAD":
                out.append((method, path))
    return out


def client(world, **kwargs) -> TestClient:
    return TestClient(world["app"], base_url=BASE, **kwargs)


def test_every_route_is_checked(world):
    found = routes(world)
    # A sanity check that the walk sees the app, not an empty list.
    assert ("POST", "/api/subjects/calculus/chat/message") in found
    assert ("GET", "/api/subjects/calculus/sessions") in found
    assert ("POST", "/api/settings/key") in found
    assert len(found) > 40


def test_changes_need_the_pages_own_origin(world):
    """Another website open in the browser can't drive velocity: every request that can
    change something is refused without velocity's own Origin."""
    c = client(world)
    for method, path in routes(world):
        if method not in CHANGES:
            continue
        for origin in ("http://evil.example", "null", "http://127.0.0.1:9999", None):
            headers = {"Origin": origin} if origin else {}
            response = c.request(method, path, headers=headers, json={})
            assert response.status_code == 403, (method, path, origin)


def test_other_hosts_are_refused(world):
    """DNS rebinding: a page on evil.example that resolves to 127.0.0.1 is refused."""
    c = TestClient(world["app"], base_url="http://evil.example:8765")
    for method, path in routes(world):
        response = c.request(
            method, path, headers={"Origin": "http://evil.example:8765"}
        )
        assert response.status_code == 400, (method, path)


def snapshot(roots) -> dict[str, tuple[int, int]]:
    files = {}
    for root in roots:
        for dirpath, _, names in os.walk(root):
            for n in names:
                p = os.path.join(dirpath, n)
                st = os.stat(p)
                files[p] = (st.st_mtime_ns, st.st_size)
    return files


def test_reading_changes_nothing(world):
    """No GET route changes a file: the vault, velocity's data and its config stay as
    they were (so a link or an image elsewhere can never change anything)."""
    c = client(world)
    c.get("/")  # anything the first request sets up
    before = snapshot(world["roots"])
    for method, path in routes(world):
        if method == "GET":
            response = c.get(path)
            assert response.status_code < 500, (path, response.text[:200])
    assert snapshot(world["roots"]) == before


def test_every_response_carries_the_security_headers(world):
    """Answers, refusals and errors alike: the policy is on everything the server sends."""
    c = client(world)
    paths = [p for m, p in routes(world) if m == "GET"]
    responses = [c.get(p) for p in paths]
    responses += [
        c.post(p, headers={"Origin": "http://evil.example"})
        for m, p in routes(world)
        if m == "POST"
    ]
    responses += [c.get("/static/app.js"), c.get("/nope"), c.get("/static/nope.js")]
    for response in responses:
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_keys_never_reach_the_page(world):
    """Keys saved in the keyring or set in the environment are used, never shown."""
    c = client(world)
    for method, path in routes(world):
        if method == "GET":
            body = c.get(path).text
            assert SECRET not in body and FREE_SECRET not in body, path
    statuses = c.get("/api/settings").json()["keys"]
    assert statuses["anthropic"]["source"] == "env"
    assert statuses["openai"]["source"] == "keyring"


@pytest.mark.parametrize(
    "path",
    [
        "/static/../web.py",
        "/static/%2e%2e/web.py",
        "/static/..%2fweb.py",
        "/static/%2e%2e%2f%2e%2e%2fpyproject.toml",
        "/static/vendor/../../web.py",
    ],
)
def test_static_files_stay_in_their_folder(world, path):
    response = client(world).get(path)
    assert response.status_code == 404 or "def build_app" not in response.text
    assert "import " not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/subjects/..%2f..%2fetc/progress",
        "/api/subjects/calculus/sources/..%2f..%2fbook.pdf/file",
        "/api/subjects/calculus/sources/%2e%2e/file",
        "/api/subjects/calculus/sessions/..%2f2026-11-12-1",
        "/api/library/..%2f..%2fx/prepare",
    ],
)
def test_names_in_addresses_never_become_paths(world, path):
    """Subjects, sources, sessions and library entries are looked up by name or id,
    never turned into a path from the request."""
    response = client(world).get(path)
    assert response.status_code in (400, 404), (path, response.status_code)


def test_the_setup_screen_follows_the_same_rules(tmp_path):
    """Before velocity is set up, the setup app has the same guards."""
    with TestClient(serve_app(None), base_url=BASE) as c:
        page = c.get("/")
        assert page.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        refused = c.post("/api/settings", json={"vault_path": str(tmp_path)})
        assert refused.status_code == 403
    with TestClient(serve_app(None), base_url="http://evil.example:8765") as c:
        assert c.get("/").status_code == 400

"""Front end C: a local web UI (M8). This part serves the page and the session history.

The server only listens on 127.0.0.1. Requests must name the local server as their host
(against DNS rebinding), and requests that change anything must come from the page itself
(same Origin), so another website open in the browser can't drive it. The vault is only
read here, through the session code.
"""

import re
from importlib.resources import files
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from velocity.config import Config, ConfigError, list_subjects, load_subject
from velocity.session import Session, record_dir

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
_SESSION_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-\d+$")
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
STATIC = Path(str(files("velocity").joinpath("web_static")))


class SameOriginMiddleware:
    """Reject requests that change state unless they come from this server's own page."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in (
            "GET",
            "HEAD",
            "OPTIONS",
        ):
            headers = dict(scope["headers"])
            origin = headers.get(b"origin", b"").decode()
            host = headers.get(b"host", b"").decode()
            if origin not in (f"http://{host}", f"https://{host}"):
                response = JSONResponse({"error": "cross-origin request"}, 403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app(config: Config) -> Starlette:
    def subject_or_404(slug: str):
        try:
            return load_subject(config, slug)
        except ConfigError:
            return None

    async def index(request: Request) -> Response:
        return FileResponse(STATIC / "index.html")

    async def subjects(request: Request) -> Response:
        out = []
        for slug in list_subjects(config):
            subject = load_subject(config, slug)
            out.append(
                {
                    "slug": slug,
                    "name": subject.name,
                    "sources": [s.name for s in subject.sources],
                }
            )
        return JSONResponse(out)

    async def sessions(request: Request) -> Response:
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        return JSONResponse(session_list(subject))

    async def session_log(request: Request) -> Response:
        subject = subject_or_404(request.path_params["slug"])
        session_id = request.path_params["session_id"]
        if subject is None or not _SESSION_ID.match(session_id):
            return JSONResponse({"error": "unknown session"}, 404)
        session = Session(
            subject.slug, session_id, record_dir(subject.slug), subject.sessions_dir
        )
        if not session.record_path.exists():
            return JSONResponse({"error": "unknown session"}, 404)
        from_log = session.log_path.exists()
        text = (
            session.log_path.read_text(encoding="utf-8")
            if from_log
            else session.rendered_text()
        )
        return JSONResponse(
            {
                "id": session_id,
                "markdown": _FRONT_MATTER.sub("", text, count=1),
                # Without the markdown log, the record is shown (learner edits are lost).
                "from_log": from_log,
            }
        )

    routes = [
        Route("/", index),
        Route("/api/subjects", subjects),
        Route("/api/subjects/{slug}/sessions", sessions),
        Route("/api/subjects/{slug}/sessions/{session_id}", session_log),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS),
        Middleware(SameOriginMiddleware),
    ]
    return Starlette(routes=routes, middleware=middleware)


def _session_order(session_id: str) -> tuple[str, int]:
    day, n = session_id.rsplit("-", 1)
    return day, int(n)


def session_list(subject) -> list[dict]:
    """The subject's sessions, newest first, with a short summary of each."""
    records = record_dir(subject.slug)
    ids = [p.stem for p in records.glob("*.jsonl") if _SESSION_ID.match(p.stem)]
    out = []
    for session_id in sorted(ids, key=_session_order, reverse=True):
        session = Session(subject.slug, session_id, records, subject.sessions_dir)
        events = session.events()
        start = next((e for e in events if e.kind == "start"), None)
        learner = [e.text for e in events if e.role == "learner"]
        if session.wrapped_up:
            ended = "wrapped-up"
        elif session.ended:
            ended = "no-wrap-up"
        else:
            ended = None  # still open, or the record ends without an end event
        out.append(
            {
                "id": session_id,
                "started": start.ts if start else None,
                "turns": len(learner),
                "first_message": learner[0][:120] if learner else "",
                "ended": ended,
                "has_recap": session.recap() is not None,
            }
        )
    return out

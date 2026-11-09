"""Front end C: a local web UI (M8): session history, and chat (chat.py) on Claude Code or
the own agent loop.

The server only listens on 127.0.0.1. Requests must name the local server as their host
(against DNS rebinding), and requests that change anything must come from the page itself
(same Origin), so another website open in the browser can't drive it. The vault is only
read here, through the session code.
"""

import asyncio
import contextlib
import json
import re
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from velocity.chat import ChatError, ChatManager, engines, last_engine
from velocity.config import Config, ConfigError, list_subjects, load_subject
from velocity.engine import CLAUDE_CODE
from velocity.session import SESSION_ID, Session, record_dir, session_summaries

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
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


def build_app(config: Config, chats: ChatManager | None = None) -> Starlette:
    chats = chats or ChatManager(config)

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
        if subject is None or not SESSION_ID.match(session_id):
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

    # -- chat --------------------------------------------------------------------------

    async def engine_list(request: Request) -> Response:
        return JSONResponse(engines())

    async def chat_status(request: Request) -> Response:
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        return JSONResponse(chats.status(subject))

    async def chat_start(request: Request) -> Response:
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        body = await _json(request)
        try:
            await chats.start(
                subject,
                model=str(body.get("model") or "sonnet"),
                resume=body.get("resume") or None,
                continues=body.get("continues") or None,
                close_other=bool(body.get("close_other")),
                engine=str(body.get("engine") or CLAUDE_CODE),
            )
        except ChatError as e:
            return JSONResponse({"error": str(e)}, e.status)
        return JSONResponse(chats.status(subject))

    async def chat_message(request: Request) -> Response:
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        text = str((await _json(request)).get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "empty message"}, 400)
        if subject.slug not in chats.chats:
            return JSONResponse({"error": "No chat is open for this subject."}, 409)

        async def events():
            async for event in chats.send(subject, text):
                yield f"data: {json.dumps(asdict(event))}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    async def chat_pause(request: Request) -> Response:
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        await chats.pause(subject)
        return JSONResponse(chats.status(subject))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async def pause_idle_chats():
            while True:
                await asyncio.sleep(60)
                await chats.pause_idle()

        task = asyncio.create_task(pause_idle_chats())
        try:
            yield
        finally:
            task.cancel()
            await chats.shutdown()  # stopping the server pauses open chats

    routes = [
        Route("/", index),
        Route("/api/subjects", subjects),
        Route("/api/engines", engine_list),
        Route("/api/subjects/{slug}/sessions", sessions),
        Route("/api/subjects/{slug}/sessions/{session_id}", session_log),
        Route("/api/subjects/{slug}/chat", chat_status),
        Route("/api/subjects/{slug}/chat/start", chat_start, methods=["POST"]),
        Route("/api/subjects/{slug}/chat/message", chat_message, methods=["POST"]),
        Route("/api/subjects/{slug}/chat/pause", chat_pause, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS),
        Middleware(SameOriginMiddleware),
    ]
    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
    app.state.chats = chats
    return app


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def session_list(subject) -> list[dict]:
    """The subject's sessions, newest first, with a short summary of each. Paused sessions
    also say which engine and model ran them last, the defaults for resuming them."""
    out = []
    for s in session_summaries(subject.slug, subject.sessions_dir):
        item = asdict(s)
        if s.ended == "no-wrap-up":
            session = Session(
                subject.slug, s.id, record_dir(subject.slug), subject.sessions_dir
            )
            item["last_engine"] = last_engine(subject, session)
        out.append(item)
    return out

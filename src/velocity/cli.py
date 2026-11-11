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


def _sessions(subject_slug: str, search: str | None = None) -> int:
    from velocity.session import search_sessions, search_words, session_summaries

    subject = load_subject(load_config(), subject_slug)
    if search is not None:
        if not search_words(search):
            print("Give some words to search for.")
            return 1
        found = [
            (m.session, m.snippet)
            for m in search_sessions(subject.slug, subject.sessions_dir, search)
        ]
        if not found:
            print(f'No sessions with "{search}".')
            return 0
    else:
        found = [
            (s, None) for s in session_summaries(subject.slug, subject.sessions_dir)
        ]
        if not found:
            print("No sessions yet.")
            return 0
    status = {"wrapped-up": "wrapped up", "no-wrap-up": "paused", None: "open"}
    for s, snippet in found:
        label = s.title or f"(untitled) {s.first_message[:60]}"
        extra = f", continues {s.continues}" if s.continues else ""
        print(f"{s.id}  {label}  [{status[s.ended]}, {s.turns} messages{extra}]")
        if snippet:
            print(f"    {snippet}")
    return 0


def _remove_empty(subject_slug: str | None) -> int:
    """List empty and wrap-up-only sessions (one subject, or all), then remove them after
    the learner confirms (decided 2026-11-10)."""
    from velocity.session import cleanup_candidates, remove_sessions

    config = load_config()
    slugs = [subject_slug] if subject_slug else list_subjects(config)
    found = []
    for slug in slugs:
        subject = load_subject(config, slug)
        candidates = cleanup_candidates(subject.slug, subject.sessions_dir)
        if not candidates:
            continue
        print(subject.name)
        for c in candidates:
            kept = f" (kept: {c.kept})" if c.kept else ""
            print(f"  {c.session_id}  {c.reason}{kept}")
        found.append((subject, [c.session_id for c in candidates if not c.kept]))
    total = sum(len(ids) for _, ids in found)
    if not total:
        print("No empty sessions to remove.")
        return 0
    answer = input(f"Remove {total} session{'s' if total != 1 else ''}? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        print("Nothing removed.")
        return 0
    removed = sum(
        remove_sessions(subject.slug, subject.sessions_dir, ids)
        for subject, ids in found
    )
    print(f"Removed {removed} session{'s' if removed != 1 else ''}.")
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
        return _recheck(
            config, load_subject(config, args.subject), args.all, args.source
        )
    if args.source_command == "prepare":
        if bool(args.subject) == bool(args.file):
            print("Name a subject, or a PDF with --file (a book in no subject yet).")
            return 1
        subject = load_subject(config, args.subject) if args.subject else None
        return _prepare(config, subject, args)
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
    """OCR part of a PDF source ahead of time, showing progress (velocity source prepare).
    With `subject` None, `args.file` names the PDF (a library book in no subject yet);
    prepared pages are kept per file, so any subject that adds it later finds them."""
    import sys
    from pathlib import Path

    from velocity.pdf_source import PdfSource
    from velocity.prepare import (
        PrepareError,
        estimate_seconds,
        format_duration,
        run_job,
    )
    from velocity.tools import default_ocr

    if subject is None:
        path = Path(args.file).expanduser().resolve()
        if path.suffix.lower() != ".pdf" or not path.is_file():
            print(f"{args.file}: not a PDF file.")
            return 1
        name = path.name
    else:
        pdfs = [s for s in subject.sources if s.path.suffix.lower() == ".pdf"]
        if args.source:
            pdfs = [s for s in pdfs if s.name == args.source]
        if len(pdfs) != 1:
            names = ", ".join(
                s.name for s in subject.sources if s.path.suffix == ".pdf"
            )
            print(
                "Name the PDF source with --source"
                + (f" (one of: {names})." if names else "; this subject has none.")
            )
            return 1
        name, path = pdfs[0].name, pdfs[0].path
    pdf = PdfSource(path, default_ocr(config))
    try:
        first, last, label = prepare_scope(pdf, args.section, args.pages)
    except ValueError as e:
        print(e)
        return 1
    todo = len(pdf.missing_pages(first, last))
    if not todo:
        print(f"{name}: {label} is already prepared.")
        return 0
    where = "" if label.startswith("PDF pp.") else f" (PDF pp. {first}-{last})"
    print(
        f"{name}: preparing {label}{where}: {todo} pages to OCR, "
        f"{format_duration(estimate_seconds(todo))}. This takes a while and keeps the "
        "GPU busy; the tutor keeps working meanwhile. Stop with Ctrl+C: finished pages "
        "are kept, and running this again continues where it stopped."
    )
    tty = sys.stdout.isatty()
    show = _progress_printer()
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
        + f"{name}: {result['state']}, {result['done']} pages OCR'd."
    )
    return 0 if result["state"] == "done" else 1


def _progress_printer():
    """A progress bar for a prepare or recheck job, updated after each batch."""
    import sys

    from velocity.prepare import format_duration

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

    return show


def _recheck(
    config, subject, all_pages: bool = False, source_name: str | None = None
) -> int:
    """Re-OCR cached pages with the dropped-text check: those from before it, or all.
    Runs as a job (prepare.py), so the web page shows its progress too."""
    import json
    import sys

    from velocity.pdf_source import PdfSource
    from velocity.prepare import (
        PrepareError,
        estimate_seconds,
        format_duration,
        run_job,
    )
    from velocity.tools import default_ocr

    refs = [s for s in subject.sources if s.path.suffix.lower() == ".pdf"]
    if source_name:
        refs = [s for s in refs if s.name == source_name]
    if not refs:
        print("No PDF source" + (f" named {source_name!r}." if source_name else "."))
        return 0 if not source_name else 1
    todo = {}
    for ref in refs:
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
            f"{ref.name}: OCR'ing {len(pages)} prepared "
            f"page{'s' if len(pages) != 1 else ''} again with the dropped-text check "
            f"({format_duration(estimate_seconds(len(pages)))})."
        )
        source = PdfSource(ref.path, ocr)
        label = (
            f"{len(pages)} prepared pages"
            if all_pages
            else f"{len(pages)} pages prepared before the dropped-text check"
        )
        try:
            result = run_job(
                source,
                1,
                source.page_count,
                label,
                on_update=_progress_printer(),
                again=pages,
            )
        except PrepareError as e:
            print(e)
            return 1
        except KeyboardInterrupt:
            print("\nStopped. Pages done so far are kept.")
            return 130
        print("\n" if sys.stdout.isatty() else "", end="")
        if result["state"] != "done":
            print(f"{ref.name}: {result['state']}.")
            return 1
        reports = [source.cache_dir / "pages" / f"{p}.check.json" for p in pages]
        regions = [
            r
            for report in reports
            if report.exists()
            for r in json.loads(report.read_text())
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
    sessions.add_argument(
        "subject",
        nargs="?",
        help="subject id, e.g. calculus (with --remove-empty: all subjects if left out)",
    )
    sessions.add_argument(
        "--remove-empty",
        action="store_true",
        help="find empty and wrap-up-only sessions, list them, and remove them after "
        "you confirm",
    )
    sessions.add_argument(
        "--search",
        metavar="WORDS",
        help="only sessions with all these words (any case, any order) in their title "
        "or log",
    )

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
    source_recheck.add_argument("--source", help="only this source (by name)")
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
    source_prepare.add_argument("subject", nargs="?")
    source_prepare.add_argument(
        "--source", help="which source (needed when the subject has several PDFs)"
    )
    source_prepare.add_argument(
        "--file",
        help="a PDF in no subject yet (instead of a subject), e.g. a library book",
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
            if args.remove_empty and args.search is not None:
                parser.error("sessions: use --search or --remove-empty, not both")
            if args.remove_empty:
                return _remove_empty(args.subject)
            if not args.subject:
                parser.error(
                    "sessions: give a subject, e.g. velocity sessions calculus"
                )
            return _sessions(args.subject, args.search)
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

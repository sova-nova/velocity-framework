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
)


def _tutor(subject_slug: str, resume: bool) -> int:
    from velocity.workspace import build_workspace, claude_args

    config = load_config()
    if subject_slug not in list_subjects(config):
        known = ", ".join(list_subjects(config)) or "none yet"
        raise ConfigError(
            f'no subject {subject_slug!r} (known: {known}). Create one with: velocity new "<name>"'
        )
    workspace = build_workspace(config, load_subject(config, subject_slug))
    claude = shutil.which("claude")
    if claude is None:
        raise ConfigError("Claude Code (`claude`) is not on PATH; install it first")
    args = [claude, *claude_args(workspace, resume)]
    os.chdir(workspace)
    os.execv(claude, args)
    return 0  # not reached


def _chat(subject_slug: str, model: str | None) -> int:
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
    asyncio.run(chat(config, subject, sys.stdout, input, model=model))
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

    from velocity.outline import toc_size

    added = add_source(config, args.subject, args.file, args.name)
    print(f"Added '{added.name}' to {args.subject}. Start a new session to use it.")
    if args.name is None:
        print(
            f'Tip: the tutor cites it as "{added.name} §<section>". To use a shorter name, edit '
            "subject.toml or remove it and add it again with --name."
        )
    entries = toc_size(added.path)
    if entries == 0:
        print(
            "Warning: this PDF has no table of contents (bookmarks), so the tutor cannot find "
            "its sections. read_section and get_exercises will not work for it yet."
        )
    else:
        print(f"Found {entries} entries in its table of contents.")
    return 0


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

    chat = commands.add_parser(
        "chat", help="tutor in the terminal via the Anthropic API (needs an API key)"
    )
    chat.add_argument("subject", help="subject id, e.g. calculus")
    chat.add_argument(
        "--model",
        help="opus, sonnet, haiku, or a full model ID (overrides [api] model in "
        "config.toml; switch during a chat with /model)",
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
            return _tutor(args.subject, args.resume)
        if args.command == "chat":
            return _chat(args.subject, args.model)
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
            print("\n".join(list_subjects(load_config())) or "No subjects yet.")
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

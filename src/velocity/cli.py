"""The `velocity` command."""

import argparse
import os
import shutil
import sys

from velocity.config import (
    ConfigError,
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

    new = commands.add_parser("new", help="create a subject")
    new.add_argument("name", help='subject name, e.g. "Network Security"')

    commands.add_parser("subjects", help="list subjects")

    mcp = commands.add_parser("mcp", help="run the tutor's MCP server over stdio")
    mcp.add_argument("--subject", required=True)

    hook = commands.add_parser("hook", help="Claude Code hook handler (internal)")
    hook.add_argument(
        "event",
        choices=["session-start", "user-prompt", "stop", "session-end", "guard"],
    )
    hook.add_argument("--subject", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "tutor":
            return _tutor(args.subject, args.resume)
        if args.command == "new":
            subject = create_subject(load_config(), args.name)
            print(
                f"Created subject '{subject.slug}'. Edit {subject.dir / 'subject.toml'} to add sources and note folders."
            )
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

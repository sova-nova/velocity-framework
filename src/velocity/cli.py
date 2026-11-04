"""The `velocity` command."""

import argparse
import sys

from velocity.config import ConfigError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="velocity", description="A local subject tutor."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    mcp = commands.add_parser("mcp", help="run the tutor's MCP server over stdio")
    mcp.add_argument("--subject", required=True, help="subject id, e.g. calculus")

    args = parser.parse_args(argv)
    try:
        if args.command == "mcp":
            from velocity.mcp_server import serve

            serve(args.subject)
    except ConfigError as e:
        print(f"velocity: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""MCP server exposing TutorTools for one subject over stdio."""

import functools
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError

from velocity.config import load_config, load_subject
from velocity.tools import ToolError, TutorTools

TOOL_NAMES = [
    "list_sources",
    "get_outline",
    "read_section",
    "get_exercises",
    "append_log",
    "list_sessions",
    "read_session",
    "check_log",
    "restore_log",
    "end_session",
    "record_question",
    "set_topic_status",
    "open_thread",
    "close_thread",
    "read_state",
]


def _expected_errors_to_mcp(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError as e:
            raise McpToolError(str(e)) from e

    return wrapper


def build_server(tools: TutorTools) -> MCPServer:
    server = MCPServer(
        name="velocity",
        instructions=(
            f"Tutor tools for the subject '{tools.subject.name}': its sources, "
            "the session log (append worked problems and recaps with append_log), "
            "and the learner's progress (record every answered question with record_question)."
        ),
    )
    for name in TOOL_NAMES:
        server.add_tool(_expected_errors_to_mcp(getattr(tools, name)), name=name)
    return server


def serve(subject_slug: str) -> None:
    config = load_config()
    build_server(TutorTools(config, load_subject(config, subject_slug))).run("stdio")

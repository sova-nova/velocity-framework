"""The own agent loop as a web chat engine (M8, PR C): the tutor from `velocity chat`, on
the Anthropic API with the learner's own key, billed per token.

Each chat runs velocity's MCP server over stdio, as `velocity chat` does, from a background
task that holds the connection open until the chat stops (the MCP client must be entered
and left in the same task). The agent calls velocity's hook functions directly, so the
session log is written exactly as in the other front ends.

Resume rebuilds the conversation from the session record (history.py), so any paused
session can be resumed here, whichever front end started it. Each reply ends with its
cost and the session's total so far, both estimates from the token counts.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager

from velocity.agent import (
    DEFAULT_EFFORT,
    StreamFactory,
    ToolCaller,
    TutorAgent,
    mcp_tool_caller,
    mcp_tool_params,
    recorded_cost,
    resolve_model,
    unknown_command,
)
from velocity.config import Config, Subject
from velocity.engine import ChatEvent, EngineError
from velocity.session import Session, active_session_id, record_dir
from velocity.skills import load_skills

MODELS = {"sonnet": "Sonnet", "opus": "Opus", "haiku": "Haiku"}
API_KEY_ENV = "ANTHROPIC_API_KEY"

# subject -> async context manager giving (tool caller, tool definitions)
Connector = Callable[[Subject], AbstractAsyncContextManager[tuple[ToolCaller, list]]]


def api_available() -> bool:
    return bool(os.environ.get(API_KEY_ENV))


@contextlib.asynccontextmanager
async def connect_mcp(subject: Subject):
    """velocity's MCP server over stdio, the same server `velocity chat` uses."""
    from mcp import Client, StdioServerParameters

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "velocity.cli", "mcp", "--subject", subject.slug],
    )
    async with Client(server) as mcp:
        yield mcp_tool_caller(mcp), mcp_tool_params((await mcp.list_tools()).tools)


class ApiChat:
    """One own-loop chat: an MCP connection and a TutorAgent, one reply at a time."""

    def __init__(
        self,
        config: Config,
        subject: Subject,
        model: str = "sonnet",
        reopen: str | None = None,
        continues: str | None = None,
        connect: Connector = connect_mcp,
        stream: StreamFactory | None = None,
    ) -> None:
        self.config = config
        self.subject = subject
        self.model = resolve_model(model)
        self.reopen = reopen
        self.continues = continues
        self.connect = connect
        self.stream = stream
        self.agent: TutorAgent | None = None
        self.session_id: str | None = None  # the velocity session, once started
        self._closing = asyncio.Event()
        self._connection: asyncio.Task | None = None
        self._turn = asyncio.Lock()

    @property
    def running(self) -> bool:
        return (
            self.agent is not None
            and self._connection is not None
            and not self._connection.done()
        )

    async def start(self) -> None:
        if self.stream is None:
            if not api_available():
                raise EngineError(
                    f"{API_KEY_ENV} is not set. Export it in the terminal that runs "
                    "velocity web, then restart it."
                )
            import anthropic

            self.stream = anthropic.AsyncAnthropic().beta.messages.stream
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        self._connection = asyncio.create_task(self._hold_connection(ready))
        try:
            call_tool, tools = await ready
        except Exception as e:  # noqa: BLE001 - the MCP client raises many kinds
            raise EngineError(f"Could not start velocity's tool server: {e}") from None
        self.agent = TutorAgent(
            config=self.config,
            subject=self.subject,
            stream=self.stream,
            call_tool=call_tool,
            tools=tools,
            skills=load_skills(),
            model=self.model,
            effort=self.config.api_effort or DEFAULT_EFFORT,
            continues=self.continues,
            reopen=self.reopen,
        )
        self.agent.start()
        self.session_id = active_session_id(self.subject.slug)

    async def _hold_connection(self, ready: asyncio.Future) -> None:
        try:
            async with self.connect(self.subject) as connection:
                ready.set_result(connection)
                await self._closing.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e)
            if not isinstance(e, Exception):
                raise

    async def send(self, text: str) -> AsyncIterator[ChatEvent]:
        """Send one learner message and stream the tutor's reply until it is done."""
        async with self._turn:
            if not self.running:
                yield ChatEvent("error", text="The tutor is not running.")
                return
            refused = self._local_reply(text)
            if refused:
                yield ChatEvent("error", text=refused)
                yield ChatEvent("done")
                return

            events: asyncio.Queue[ChatEvent | None] = asyncio.Queue()
            self.agent.on_event = events.put_nowait
            turn = asyncio.create_task(self.agent.turn(text))
            turn.add_done_callback(lambda _: events.put_nowait(None))
            try:
                while (event := await events.get()) is not None:
                    yield event
            finally:
                # The page went away mid-reply: stop the turn (the agent rolls it back).
                if not turn.done():
                    turn.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await turn
                self.agent.on_event = None
            if turn.exception() is not None:
                yield ChatEvent("error", text=_api_error(turn.exception()))
                return
            yield ChatEvent("done", cost=self._cost())

    def _local_reply(self, text: str) -> str | None:
        """Commands answered here instead of by the tutor (the page has no /model)."""
        command = text.strip().split(maxsplit=1)[0] if text.strip() else ""
        if command in ("/model", "/exit", "/quit"):
            return (
                "Pick the model when you start or resume a session; end with End "
                "session or Pause."
            )
        return unknown_command(text, self.agent.skills)

    def _cost(self) -> dict:
        session = Session(
            self.subject.slug,
            self.session_id,
            record_dir(self.subject.slug),
            self.subject.sessions_dir,
        )
        return {
            "reply": self.agent.last_turn_usage.cost(self.model),
            "total": recorded_cost(session),
        }

    async def stop(self) -> None:
        """Close the session log (unless already wrapped up) and the tool server."""
        if self.agent is not None:
            self.agent.finish()
        self._closing.set()
        if self._connection is not None:
            with contextlib.suppress(Exception):
                await self._connection


def _api_error(error: BaseException) -> str:
    import anthropic

    if isinstance(error, anthropic.APIConnectionError):
        return "Network error; your message was not answered. Try again."
    if isinstance(error, anthropic.RateLimitError):
        return "Rate limited; wait a moment and try again."
    if isinstance(error, anthropic.APIStatusError):
        return f"API error {error.status_code}: {error.message}"
    return f"The tutor failed: {error}"

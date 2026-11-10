"""What a web chat engine is: Claude Code headless (claude_engine.py) or the own agent loop
(api_engine.py). The chat manager (chat.py) drives either through this interface."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

CLAUDE_CODE = "claude-code"
OWN_LOOP = "api"
# Ends a reply the learner stopped, in the log (the partial text before it is kept).
STOPPED_MARK = "*(stopped)*"


@dataclass(frozen=True)
class ChatEvent:
    """What the web page sees: streamed text, a tool call, the end of a reply, an error."""

    type: str  # "text", "tool", "note", "done", "stopped", "error", "ended"
    text: str = ""
    name: str = ""
    input: dict | None = None
    # On "done" from the own loop: {"reply": $ for this reply, "total": $ for the session}.
    cost: dict | None = None


class EngineError(Exception):
    pass


class Engine(Protocol):
    @property
    def running(self) -> bool: ...

    async def start(self) -> None: ...

    def send(self, text: str) -> AsyncIterator[ChatEvent]:
        """Send one learner message; stream the reply, ending with "done", "stopped"
        (after interrupt) or "error"."""
        ...

    async def interrupt(self) -> None:
        """Stop the reply being streamed, if any; `send` then ends with "stopped"."""
        ...

    async def stop(self) -> None:
        """End the chat without a wrap-up; the session can be resumed later."""
        ...

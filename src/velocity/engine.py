"""What a web chat engine is: Claude Code headless (claude_engine.py) or the own agent loop
(api_engine.py). The chat manager (chat.py) drives either through this interface."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

CLAUDE_CODE = "claude-code"
OWN_LOOP = "api"


@dataclass(frozen=True)
class ChatEvent:
    """What the web page sees: streamed text, a tool call, the end of a reply, an error."""

    type: str  # "text", "tool", "done", "error", "ended"
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
        """Send one learner message; stream the reply, ending with "done" or "error"."""
        ...

    async def stop(self) -> None:
        """End the chat without a wrap-up; the session can be resumed later."""
        ...

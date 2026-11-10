"""The Claude Code engine for web chat: headless Claude Code as the tutor, on the learner's
own plan, in the same generated workspace `velocity tutor` uses.

One long-running `claude -p` process per chat, with stream-json in and out (verified
2026-11-06): each learner message is one JSON line on stdin; the reply streams back as
events and ends with a `result` event; the process then waits for the next message.
Claude Code runs velocity's hooks, so the session log is written exactly as in
`velocity tutor`. Closing stdin ends the process, and the SessionEnd hook closes the log.
"""

import asyncio
import itertools
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from velocity.engine import ChatEvent, EngineError
from velocity.workspace import claude_args

__all__ = ["MODELS", "ChatEvent", "ClaudeCodeChat", "EngineError", "parse_event"]

MODELS = {"sonnet": "Sonnet", "opus": "Opus"}
STOP_TIMEOUT = 30  # seconds to wait for Claude Code (and its SessionEnd hook) to exit
_REQUEST_IDS = itertools.count(1)


def claude_command(
    workspace: Path, model: str, resume_id: str | None = None
) -> list[str]:
    claude = shutil.which("claude")
    if claude is None:
        raise EngineError("Claude Code (`claude`) is not on PATH; install it first.")
    command = [
        claude,
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", model,
        # Nothing can answer a permission prompt here: deny instead of waiting.
        "--permission-prompts", "none",
    ]  # fmt: skip
    if resume_id:
        command += ["--resume", resume_id]
    return command + claude_args(workspace)


def parse_event(line: str) -> list[ChatEvent]:
    """The chat events in one line of Claude Code's stream-json output."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return []
    kind = event.get("type")
    if kind == "stream_event":
        inner = event.get("event") or {}
        delta = inner.get("delta") or {}
        if (
            inner.get("type") == "content_block_delta"
            and delta.get("type") == "text_delta"
        ):
            return [ChatEvent("text", text=delta.get("text", ""))]
        return []
    if kind == "assistant":
        # Complete messages repeat the streamed text; only their tool calls are new.
        content = (event.get("message") or {}).get("content") or []
        return [
            ChatEvent(
                "tool", name=_short_tool_name(b.get("name", "")), input=b.get("input")
            )
            for b in content
            if b.get("type") == "tool_use" and _shown(b.get("name", ""))
        ]
    if kind == "result":
        if event.get("is_error") or event.get("subtype") != "success":
            message = event.get("result") or event.get("subtype") or "unknown error"
            return [ChatEvent("error", text=f"Claude Code: {message}")]
        return [ChatEvent("done")]
    return []


def _short_tool_name(name: str) -> str:
    return name.removeprefix("mcp__velocity__")


def _shown(name: str) -> bool:
    """The page shows the tutor's own tools and skills, not Claude Code's internal
    steps (ToolSearch and the like), which mean nothing to a learner (M10)."""
    return name.startswith("mcp__velocity__") or name == "Skill"


class ClaudeCodeChat:
    """One headless Claude Code process, one reply at a time."""

    def __init__(
        self,
        workspace: Path,
        model: str = "sonnet",
        resume_id: str | None = None,
        env: dict[str, str] | None = None,
        command: list[str] | None = None,
    ) -> None:
        self.workspace = workspace
        self.model = model
        self.resume_id = resume_id
        self.env = env
        self.command = command or claude_command(workspace, model, resume_id)
        self.claude_session_id: str | None = resume_id
        self._process: asyncio.subprocess.Process | None = None
        self._turn = asyncio.Lock()
        self._interrupted = False  # the current reply was interrupted

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=self.workspace,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,  # tool results can make long lines
        )

    async def send(self, text: str) -> AsyncIterator[ChatEvent]:
        """Send one learner message and stream the tutor's reply until it is done."""
        async with self._turn:
            if not self.running:
                yield ChatEvent("error", text="The tutor process is not running.")
                return
            self._interrupted = False
            await self._write(
                {"type": "user", "message": {"role": "user", "content": text}}
            )
            while True:
                raw = await self._process.stdout.readline()
                if not raw:
                    stderr = (await self._process.stderr.read()).decode()[-500:]
                    yield ChatEvent(
                        "error",
                        text="Claude Code stopped unexpectedly."
                        + (f" {stderr.strip()}" if stderr.strip() else ""),
                    )
                    return
                decoded = raw.decode(errors="replace")
                self._note_session_id(decoded)
                for event in parse_event(decoded):
                    if event.type == "error" and self._interrupted:
                        # An interrupted reply ends with an error result.
                        yield ChatEvent("stopped")
                        return
                    yield event
                    if event.type in ("done", "error"):
                        return

    async def interrupt(self) -> None:
        """Stop the reply being streamed (verified 2026-11-09): Claude Code acknowledges
        an interrupt control request, ends the reply with an error result, and waits
        for the next message. Its Stop hook doesn't run, so the caller logs the partial
        reply."""
        if not self.running or not self._turn.locked() or self._interrupted:
            return
        self._interrupted = True
        await self._write(
            {
                "type": "control_request",
                "request_id": f"interrupt-{next(_REQUEST_IDS)}",
                "request": {"subtype": "interrupt"},
            }
        )

    async def _write(self, message: dict) -> None:
        self._process.stdin.write((json.dumps(message) + "\n").encode())
        await self._process.stdin.drain()

    def _note_session_id(self, line: str) -> None:
        if self.claude_session_id is None and '"session_id"' in line:
            try:
                self.claude_session_id = json.loads(line).get("session_id")
            except json.JSONDecodeError:
                pass

    async def stop(self) -> None:
        """End the process by closing its input; Claude Code then runs SessionEnd."""
        if not self.running:
            return
        self._process.stdin.close()
        try:
            await asyncio.wait_for(self._process.wait(), STOP_TIMEOUT)
        except TimeoutError:
            self._process.kill()
            await self._process.wait()

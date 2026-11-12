"""Front end B: velocity's own agent loop on the Anthropic API.

The same tutor as the Claude Code front end, without Claude Code:
- the system prompt is the same tutor template, plus a catalogue of skills;
- tools come from velocity's MCP server over stdio (the same server Claude Code uses), plus a
  local `load_skill` tool;
- the work Claude Code's hooks do (open the log, log each turn verbatim, pass annotations, close
  the log) is done by calling the same hook functions directly;
- session context, annotations, and invoked skills are added as mid-conversation system
  messages, so the cached prefix (system prompt + tools + history) stays intact.

The loop owns the conversation history and only ever appends to it.
"""

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from importlib.resources import files
from typing import Any, Protocol, TextIO

from velocity import hooks
from velocity.config import Config, Subject
from velocity.engine import STOPPED_MARK, ChatEvent
from velocity.history import rebuild_messages
from velocity.session import Session, active_session_id, record_dir
from velocity.skills import Skill, parse_slash_command
from velocity.workspace import render_claude_md

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 64_000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept `fallbacks: "default"` on the Claude API.
FALLBACK_MODELS = {
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
}
# $ per million tokens: input, output, cache read. Cache writes (5-minute TTL) cost 1.25x input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
# Short names accepted by `--model`, `/model`, and `[api] model`.
MODEL_ALIASES = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
}
MAX_JSON_RETRIES = 2
# Server-side compaction at a token threshold (decided 2026-11-10): when a request's input
# reaches COMPACT_AT tokens, the API summarizes the older turns (with the tutor's prompt in
# template/compaction.md) and the summary replaces them from then on.
COMPACT_BETA = "compact-2026-01-12"
COMPACT_AT = 150_000
COMPACT_MODELS = {
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-fable-5",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-5-5",
}
COMPACTED_NOTE = "Earlier conversation summarized to keep the chat short."


def resolve_model(name: str) -> str:
    """A model alias or full Claude model ID -> the full ID. Raises ValueError otherwise."""
    name = name.strip()
    if name.lower() in MODEL_ALIASES:
        return MODEL_ALIASES[name.lower()]
    if name.startswith("claude-"):
        return name
    raise ValueError(
        f"Unknown model {name!r}. Use {', '.join(MODEL_ALIASES)} or a full ID like "
        "claude-sonnet-5-5."
    )


# (tool name, arguments) -> (result, is_error). The result is text, or a list of text
# and image content blocks when a tool returns images (view_page).
ToolCaller = Callable[[str, dict], Awaitable[tuple[str | list[dict], bool]]]


class StreamFactory(Protocol):
    def __call__(self, **params: Any) -> Any:
        """An async context manager like `client.beta.messages.stream(**params)`."""
        ...


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    requests: int = 0

    def add(self, usage: Any) -> None:
        # With compaction on, the top-level counts leave out a compaction step; the
        # per-iteration counts include it, so they are summed instead when present.
        for part in getattr(usage, "iterations", None) or [usage]:
            self.input += getattr(part, "input_tokens", 0) or 0
            self.output += getattr(part, "output_tokens", 0) or 0
            self.cache_read += getattr(part, "cache_read_input_tokens", 0) or 0
            self.cache_write += getattr(part, "cache_creation_input_tokens", 0) or 0
        self.requests += 1

    def cost(self, model: str) -> float | None:
        if model not in PRICES:
            return None
        price_in, price_out, price_read = PRICES[model]
        return (
            self.input * price_in
            + self.cache_write * price_in * 1.25
            + self.cache_read * price_read
            + self.output * price_out
        ) / 1_000_000

    def summary(self, model: str) -> str:
        cost = self.cost(model)
        estimate = f", about ${cost:.2f}" if cost is not None else ""
        return (
            f"{self.requests} API requests: {self.input + self.cache_read + self.cache_write:,} input "
            f"tokens ({self.cache_read:,} from cache), {self.output:,} output tokens{estimate} "
            "(estimate)"
        )


@dataclass
class UsageByModel:
    """Token usage per model, for chats that switch models with /model."""

    models: dict[str, Usage] = field(default_factory=dict)

    def add(self, model: str, usage: Any) -> None:
        self.models.setdefault(model, Usage()).add(usage)

    @property
    def requests(self) -> int:
        return sum(u.requests for u in self.models.values())

    def summary(self) -> str:
        if not self.models:
            return "No API requests."
        lines = [f"{m}: {u.summary(m)}" for m, u in self.models.items()]
        costs = [u.cost(m) for m, u in self.models.items()]
        if len(lines) > 1 and None not in costs:
            lines.append(f"Total: about ${sum(costs):.2f} (estimate)")
        return "\n".join(lines)


def recorded_cost(session: Session) -> float:
    """The estimated cost of a session's own-loop turns so far, from its record (turns on
    models without a known price count as 0)."""
    return sum(
        (e.meta or {}).get("cost") or 0.0
        for e in session.events()
        if e.role == "event" and e.kind == "usage"
    )


CHAT_COMMANDS = ("model", "exit", "quit")


def unknown_command(text: str, skills: dict[str, Skill]) -> str | None:
    """A message for a `/word` that is neither a skill nor a chat command, else None.
    Unknown commands are answered locally instead of being sent to the tutor."""
    m = re.match(r"^/([a-z][a-z0-9-]*)(?:\s|$)", text.strip())
    if not m or m[1] in skills or m[1] in CHAT_COMMANDS:
        return None
    if m[1] in MODEL_ALIASES:
        return f"Unknown command /{m[1]}. To switch models, type /model {m[1]}."
    commands = ", ".join("/" + c for c in [*skills, *CHAT_COMMANDS])
    return f"Unknown command /{m[1]}. Commands: {commands}."


def system_prompt(subject: Subject, skills: dict[str, Skill]) -> str:
    catalogue = "\n".join(f"- `{s.name}`: {s.description}" for s in skills.values())
    return (
        render_claude_md(subject) + "\n## Skills in this interface\n\n"
        "When the learner types a skill's slash command, its instructions are added to the "
        "conversation for you. When a skill fits what the learner asks for without the slash "
        "command, call `load_skill` with its name first, then follow the instructions.\n\n"
        + catalogue
        + "\n"
    )


LOAD_SKILL_TOOL = {
    "name": "load_skill",
    "description": "Load the full instructions of a skill by name (see the skills list).",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name, e.g. quiz"}
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}


def mcp_tool_params(tools: list[Any]) -> list[dict]:
    """Anthropic tool definitions for MCP tools, in a stable order (keeps the cache valid)."""
    params = [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.input_schema,
            "eager_input_streaming": True,
        }
        for t in sorted(tools, key=lambda t: t.name)
    ]
    return params + [{**LOAD_SKILL_TOOL, "eager_input_streaming": True}]


def echo_content(content: list[Any]) -> list[Any]:
    """Assistant content to send back. After a mid-output fallback, model-internal blocks
    (thinking, tool_use, ...) before the last fallback marker must be dropped; text and
    compaction summaries stay."""
    last = max((i for i, b in enumerate(content) if b.type == "fallback"), default=None)
    if last is None:
        return list(content)
    return [
        b for i, b in enumerate(content) if i > last or b.type in ("text", "compaction")
    ]


def _block_type(block: Any) -> str | None:
    """A content block's type, whether it is an SDK object or a dict."""
    return (
        block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
    )


def compaction_instructions() -> str:
    """The tutor's summarization prompt for compaction (template/compaction.md)."""
    return (
        files("velocity").joinpath("template/compaction.md").read_text(encoding="utf-8")
    )


@dataclass
class TutorAgent:
    config: Config
    subject: Subject
    stream: StreamFactory
    call_tool: ToolCaller
    tools: list[dict]
    skills: dict[str, Skill]
    out: TextIO | None = None  # the terminal (velocity chat)
    on_event: Callable[[ChatEvent], None] | None = None  # the web page (api_engine.py)
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    messages: list[dict] = field(default_factory=list)
    usage: UsageByModel = field(default_factory=UsageByModel)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Every tool call as (name, arguments), including load_skill; read by the evals.
    tool_log: list[tuple[str, dict]] = field(default_factory=list)
    continues: str | None = None  # a finished session this one picks up from
    reopen: str | None = None  # a paused session to resume, with its history rebuilt
    web_ui: bool = False  # in the web UI (it shows the reminder, and a source reader)
    last_turn_usage: Usage = field(default_factory=Usage)
    stop_requested: bool = False  # the learner stopped the running turn
    closed_empty: bool = False  # /end-session closed an empty session (it was removed)
    _streamed: str = ""  # the running turn's text so far
    _pending_context: list[str] = field(default_factory=list)

    # -- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        """Open the session log (like Claude Code's SessionStart hook). To resume a paused
        session, its conversation is first rebuilt from the session record."""
        if self.reopen:
            session = Session(
                self.subject.slug,
                self.reopen,
                record_dir(self.subject.slug),
                self.subject.sessions_dir,
            )
            tool_names = {t["name"] for t in self.tools}
            self.messages = rebuild_messages(session, tool_names)
        result = hooks.session_start(
            self.config,
            self.subject,
            {
                "session_id": self.session_id,
                "source": "startup",
                "continues": self.continues,
                "reopen": self.reopen,
                "history": bool(self.reopen),
                "web_ui": self.web_ui,
            },
        )
        self._pending_context.append(result["hookSpecificOutput"]["additionalContext"])

    def finish(self) -> None:
        """Close the session log if it is still open (like the SessionEnd hook)."""
        hooks.session_end(self.config, self.subject, {"reason": "prompt_input_exit"})

    @property
    def ended(self) -> bool:
        return active_session_id(self.subject.slug) is None

    @property
    def log_path(self) -> str:
        session_id = active_session_id(self.subject.slug)
        return (
            (self.subject.sessions_dir / f"{session_id}.md").as_posix()
            if session_id
            else ""
        )

    def model_command(self, args: str) -> str:
        """Handle `/model [name]`: show the current model, or switch to another one."""
        if not args.strip():
            aliases = ", ".join(f"{a} ({m})" for a, m in MODEL_ALIASES.items())
            return f"Current model: {self.model}. Switch with /model <name>: {aliases}."
        try:
            model = resolve_model(args)
        except ValueError as e:
            return str(e)
        if model == self.model:
            return f"Already using {model}."
        self.model = model
        note = "" if model in PRICES else " (no price known, so no cost estimate)"
        return (
            f"Switched to {model}{note}. The next message rebuilds the prompt cache, "
            "so it costs a little more."
        )

    # -- one learner turn ----------------------------------------------------------

    async def turn(self, text: str) -> str:
        """Handle one learner message; return the tutor's reply text (as logged)."""
        logged = hooks.user_prompt(
            self.config, self.subject, {"session_id": self.session_id, "prompt": text}
        )
        if logged.get("decision") == "block":
            # /end-session in an empty session: close it (and so remove it) instead.
            self._write("\n" + hooks.NOTHING_TO_WRAP_UP + "\n")
            self.finish()
            self.closed_empty = True
            return ""
        if logged:
            self._pending_context.append(
                logged["hookSpecificOutput"]["additionalContext"]
            )

        start = len(self.messages)
        self.messages.append({"role": "user", "content": text})
        invoked = parse_slash_command(text, self.skills)
        if invoked:
            skill, args = invoked
            self._pending_context.append(
                f"The learner invoked the skill `{skill.name}`"
                + (f" with arguments: {args}" if args else " without arguments")
                + f". Follow these instructions:\n\n{skill.instructions}"
            )
        contexts, self._pending_context = self._pending_context, []
        for context in contexts:
            self.messages.append({"role": "system", "content": context})

        self.last_turn_usage = Usage()
        self._streamed = ""
        self.stop_requested = False
        try:
            reply, completed = await self._run_until_done()
        except asyncio.CancelledError:
            if not self.stop_requested:
                self._roll_back(start, contexts)
                self._record_usage()
                raise
            # The learner pressed Stop: keep the reply so far, in the log and history.
            asyncio.current_task().uncancel()
            reply, completed = self._keep_stopped(start + 1 + len(contexts)), True
        except BaseException:
            self._roll_back(start, contexts)
            self._record_usage()
            raise
        if not completed:
            self._roll_back(start, contexts)
        # Before the Stop hook, which closes the session after a wrap-up.
        self._record_usage()
        hooks.stop(
            self.config,
            self.subject,
            {"session_id": self.session_id, "last_assistant_message": reply},
        )
        return reply

    def stop_reply(self) -> None:
        """Mark the running turn as stopped by the learner; the caller then cancels it.
        Unlike an error, a stopped turn is kept (decided 2026-11-09)."""
        self.stop_requested = True

    def _keep_stopped(self, reply_start: int) -> str:
        """End a stopped turn: the text streamed so far replaces whatever the turn had
        added after the learner's message (tool calls in it are in the session record,
        not in the history). A compaction summary made during the turn is kept, so the
        next request doesn't pay to summarize again. Returns the reply as logged, with the
        stopped mark."""
        summaries = [
            b
            for m in self.messages[reply_start:]
            if m["role"] == "assistant" and isinstance(m["content"], list)
            for b in m["content"]
            if _block_type(b) == "compaction"
        ]
        del self.messages[reply_start:]
        partial = self._streamed.strip()
        text = (
            partial + "\n\n" if partial else ""
        ) + "[The learner stopped this reply here.]"
        self.messages.append(
            {
                "role": "assistant",
                "content": [summaries[-1], {"type": "text", "text": text}]
                if summaries
                else text,
            }
        )
        return (partial + "\n\n" if partial else "") + STOPPED_MARK

    def _record_usage(self) -> None:
        """Keep this turn's token usage and cost estimate in the session record, so the
        web page can show a session total across reloads and resumes."""
        usage = self.last_turn_usage
        session_id = active_session_id(self.subject.slug)
        if not usage.requests or not session_id:
            return
        session = Session(
            self.subject.slug,
            session_id,
            record_dir(self.subject.slug),
            self.subject.sessions_dir,
        )
        session.append(
            "event",
            "usage",
            kind="usage",
            meta={**asdict(usage), "model": self.model, "cost": usage.cost(self.model)},
        )

    def _roll_back(self, start: int, contexts: list[str]) -> None:
        """Drop an unanswered turn from the history (it was never accepted by the API), so the
        next request is still valid. Its context is re-sent with the next message."""
        del self.messages[start:]
        self._pending_context = contexts + self._pending_context

    async def _run_until_done(self) -> tuple[str, bool]:
        """Run requests and tools until the model ends its turn. Returns (reply text, completed)."""
        texts: list[str] = []
        json_retries = 0
        while True:
            try:
                response = await self._request()
            except ValueError:
                # Tool input JSON the SDK could not parse at all: re-issue the request.
                json_retries += 1
                if json_retries > MAX_JSON_RETRIES:
                    raise
                continue
            json_retries = 0
            self.usage.add(self.model, response.usage)
            self.last_turn_usage.add(response.usage)

            if response.stop_reason == "refusal":
                self._write("\n[The model declined to continue this turn.]\n")
                self._emit(
                    ChatEvent("error", text="The model declined to continue this turn.")
                )
                return "\n\n".join(texts), False
            self.messages.append(
                {"role": "assistant", "content": echo_content(response.content)}
            )
            texts += [
                b.text for b in response.content if b.type == "text" and b.text.strip()
            ]

            if response.stop_reason == "pause_turn":
                continue
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return "\n\n".join(texts), True
            if response.stop_reason == "max_tokens":
                # A truncated tool input parses as a valid-looking partial object; don't run it.
                results = [
                    self._result(
                        b.id, "Tool input was cut off (max_tokens); try again.", True
                    )
                    for b in tool_uses
                ]
            else:
                results = [await self._run_tool(b) for b in tool_uses]
            self.messages.append({"role": "user", "content": results})

    async def _request(self) -> Any:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": self.system,
            "tools": self.tools,
            "messages": self.messages,
            "output_config": {"effort": self.effort},
            "cache_control": {"type": "ephemeral"},
        }
        betas = []
        if self.model in FALLBACK_MODELS:
            betas.append(FALLBACK_BETA)
            params["fallbacks"] = "default"
        if self.model in COMPACT_MODELS:
            betas.append(COMPACT_BETA)
            params["context_management"] = {
                "edits": [
                    {
                        "type": "compact_20260112",
                        "trigger": {"type": "input_tokens", "value": COMPACT_AT},
                        "instructions": compaction_instructions(),
                    }
                ]
            }
        if betas:
            params["betas"] = betas
        async with self.stream(**params) as stream:
            started = False
            async for event in stream:
                if (
                    event.type == "content_block_start"
                    and _block_type(event.content_block) == "compaction"
                ):
                    self._write(f"\n[{COMPACTED_NOTE}]\n")
                    self._emit(ChatEvent("note", text=COMPACTED_NOTE))
                elif event.type == "text":
                    if not started:
                        self._write("\n")
                        started = True
                    self._write(event.text)
                    self._emit(ChatEvent("text", text=event.text))
                    self._streamed += event.text
            response = await stream.get_final_message()
        if started:
            self._write("\n")
            # Text blocks of one reply that are separated by tool calls are joined by
            # a blank line in the logged reply; keep the page's text the same.
            self._emit(ChatEvent("text", text="\n\n"))
            self._streamed += "\n\n"
        return response

    def _write(self, text: str) -> None:
        if self.out:
            self.out.write(text)
            self.out.flush()

    def _emit(self, event: ChatEvent) -> None:
        if self.on_event:
            self.on_event(event)

    async def _run_tool(self, block: Any) -> dict:
        args = block.input if isinstance(block.input, dict) else None
        if args is None:
            return self._result(
                block.id, json.dumps({"INVALID_JSON": str(block.input)}), True
            )
        result = await self._call(block, args)
        # Like Claude Code's PostToolUse hook: keep the call in the session record.
        hooks.tool_use(
            self.config,
            self.subject,
            {
                "tool_name": block.name,
                "tool_input": args,
                "is_error": result.get("is_error", False),
            },
        )
        return result

    async def _call(self, block: Any, args: dict) -> dict:
        self.tool_log.append((block.name, args))
        self._write(
            f"  · {block.name}({', '.join(f'{k}={v!r}'[:60] for k, v in args.items())})\n"
        )
        self._emit(ChatEvent("tool", name=block.name, input=args))
        if block.name == "load_skill":
            skill = self.skills.get(str(args.get("name", "")))
            if skill is None:
                names = ", ".join(self.skills)
                return self._result(
                    block.id,
                    f"No skill named {args.get('name')!r}. Skills: {names}",
                    True,
                )
            return self._result(block.id, skill.instructions, False)
        text, is_error = await self.call_tool(block.name, args)
        return self._result(block.id, text, is_error)

    @staticmethod
    def _result(tool_use_id: str, content: str | list[dict], is_error: bool) -> dict:
        result = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        if is_error:
            result["is_error"] = True
        return result

    @property
    def system(self) -> str:
        return system_prompt(self.subject, self.skills)


# -- wiring for the `velocity chat` command ------------------------------------------


def mcp_tool_caller(mcp: Any) -> ToolCaller:
    """A ToolCaller that runs tools on a connected `mcp.Client`."""

    async def call_tool(name: str, args: dict) -> tuple[str | list[dict], bool]:
        result = await mcp.call_tool(name, args)
        return tool_result_content(result.content), bool(result.is_error)

    return call_tool


def tool_result_content(content: list[Any]) -> str | list[dict]:
    """MCP content as tool_result content: plain text, or text and image blocks."""
    texts = [c.text for c in content if getattr(c, "type", "") == "text"]
    if not any(getattr(c, "type", "") == "image" for c in content):
        return "\n".join(texts)
    blocks = []
    for c in content:
        if c.type == "text":
            blocks.append({"type": "text", "text": c.text})
        elif c.type == "image":
            source = {"type": "base64", "media_type": c.mime_type, "data": c.data}
            blocks.append({"type": "image", "source": source})
    return blocks


RECAP_MESSAGES = 3
RESUME_COST_NOTE = (
    "Your first reply re-sends the session so far, so it costs more than the rest."
)


def resume_recap(session: Session, cost_note: bool = True) -> str:
    """What `velocity chat --resume` prints before the first prompt (decided 2026-11-10):
    the session's name, its last few messages in full, and the cost of the first reply
    (not on the free engine, which costs nothing)."""
    name = f'"{session.title}" ({session.id})' if session.title else session.id
    lines = [f"Resuming {name}."]
    messages = [e for e in session.events() if e.role in ("learner", "tutor")]
    last = sorted(messages, key=lambda e: e.ts)[-RECAP_MESSAGES:]
    if last:
        n = len(last)
        lines.append(
            f"Where you left off (the last {n} message{'s' if n > 1 else ''}):"
        )
        for e in last:
            who = "you" if e.role == "learner" else "tutor"
            lines.append(f"\n{who}> {e.text.strip()}")
    if cost_note:
        lines.append(f"\n{RESUME_COST_NOTE}")
    return "\n".join(lines) + "\n"


async def chat(
    config: Config,
    subject: Subject,
    out: TextIO,
    read_line: Callable[[str], str],
    model: str | None = None,
    continues: str | None = None,
    reopen: str | None = None,
) -> None:
    """Run an interactive tutoring session in the terminal. `model` (an alias or full ID)
    overrides `[api] model` in config.toml; `continues` names a finished session to pick
    up from; `reopen` names a paused one to resume, its conversation rebuilt from the
    record."""
    import asyncio
    import os

    import anthropic
    from mcp import Client, StdioServerParameters

    from velocity import keys
    from velocity.runtime import self_command
    from velocity.skills import load_skills

    command, *args = self_command("mcp", "--subject", subject.slug)
    server = StdioServerParameters(
        command=command,
        args=args,
        env=dict(os.environ),  # all of it, as in api_engine.connect_mcp
    )
    # The environment variable, else the key saved in Settings (the system keyring).
    client = anthropic.AsyncAnthropic(api_key=keys.anthropic_key())
    async with Client(server) as mcp:
        skills = load_skills()
        agent = TutorAgent(
            config=config,
            subject=subject,
            stream=client.beta.messages.stream,
            call_tool=mcp_tool_caller(mcp),
            tools=mcp_tool_params((await mcp.list_tools()).tools),
            skills=skills,
            out=out,
            model=resolve_model(model or config.api_model or DEFAULT_MODEL),
            effort=config.api_effort or DEFAULT_EFFORT,
            continues=continues,
            reopen=reopen,
        )
        agent.start()
        out.write(
            f"velocity: {subject.name} with {agent.model} (effort {agent.effort}).\n"
            f"Log: {agent.log_path}\n"
            f"Skills: {', '.join('/' + s for s in skills)}. "
            "Switch models with /model. Exit with /exit or Ctrl+D.\n"
        )
        if reopen:
            out.write(
                "\n"
                + resume_recap(
                    Session(
                        subject.slug,
                        reopen,
                        record_dir(subject.slug),
                        subject.sessions_dir,
                    )
                )
            )
        try:
            while not agent.ended:
                try:
                    line = await asyncio.to_thread(read_line, "\nyou> ")
                except EOFError:
                    break
                if line.strip() in ("/exit", "/quit"):
                    break
                if not line.strip():
                    continue
                if message := unknown_command(line, skills):
                    out.write(message + "\n")
                    continue
                if line.strip() == "/model" or line.strip().startswith("/model "):
                    out.write(agent.model_command(line.strip()[len("/model") :]) + "\n")
                    continue
                try:
                    await agent.turn(line)
                except anthropic.APIConnectionError:
                    out.write(
                        "\n[Network error; your message was not answered. Try again.]\n"
                    )
                except anthropic.RateLimitError:
                    out.write("\n[Rate limited; wait a moment and try again.]\n")
                except anthropic.APIStatusError as e:
                    out.write(f"\n[API error {e.status_code}: {e.message}]\n")
            if agent.ended and not agent.closed_empty:
                out.write("\nSession wrapped up.\n")
        finally:
            agent.finish()
            out.write(f"\n{agent.usage.summary()}\n")

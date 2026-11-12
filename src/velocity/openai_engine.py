"""The free engine (decided 2026-11-11): the tutor on any OpenAI-compatible server, such as
Ollama or llama.cpp's `llama-server` on the learner's computer, or a hosted free tier
(OpenRouter). It costs nothing, and it is weaker: small models follow the long tutor prompt
less reliably, use tools less consistently, and make more mistakes.

It is its own small agent loop, next to the Anthropic one in agent.py, and shares
everything else with it: velocity's MCP tools, the hooks that write the session log, the
skills, the tutor prompt, and the history rebuilt from a session record on resume. What
it doesn't have: prompt caching, compaction, cost estimates. Instead:
- velocity's notes for the tutor (session context, annotations, skills) go in the
  learner's message, wrapped in <velocity-note> tags; many local chat templates refuse a
  system message anywhere but first;
- a bad tool call (arguments that aren't JSON, a tool that doesn't exist) goes back to the
  model as an error, at most MAX_TOOL_RETRIES times in a turn, then the turn ends;
- a chat near the model's context size gets a note suggesting a new session; past it,
  the oldest turns are left out (never the first, which holds the session's start);
- the model's thinking (a `reasoning` field, or <think> tags) is never shown or logged;
- `view_page` is offered only when the settings say the model can see images.
"""

import asyncio
import contextlib
import dataclasses
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from velocity import hooks, keys
from velocity.agent import ToolCaller, resume_recap, system_prompt, unknown_command
from velocity.api_engine import ApiChat, Connector, connect_mcp
from velocity.config import Config, Subject
from velocity.engine import FREE, STOPPED_MARK, ChatEvent, EngineError
from velocity.history import rebuild_messages
from velocity.session import Session, active_session_id, record_dir
from velocity.skills import Skill, load_skills, parse_slash_command

LABEL = "Free model (weaker)"
NOTE = (
    "Smaller models make more mistakes, including grading wrong answers as right. "
    "Double-check anything important."
)
# Ready-made servers for the settings (decided 2026-11-11). Not endorsements: free tiers
# and their models change often.
PRESETS = [
    {
        "id": "ollama",
        "label": "Ollama (on this computer)",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3:4b",
        "api_key_env": "",
        "link": "https://ollama.com/download",
    },
    {
        "id": "llama-server",
        "label": "llama.cpp server (on this computer)",
        "base_url": "http://localhost:8080/v1",
        "model": "",
        "model_hint": "the name llama-server gives its model (any name works)",
        "api_key_env": "",
        "link": "https://github.com/ggml-org/llama.cpp",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter (hosted; models ending in :free)",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "",
        "model_hint": "a model id ending in :free, from the list behind Get it",
        "api_key_env": "OPENROUTER_API_KEY",
        "link": "https://openrouter.ai/models?max_price=0",
    },
]
MAX_TOOL_RETRIES = 2  # bad tool calls in one turn before it ends
MAX_ROUNDS = 20  # requests in one turn (each tool round is one)
LONG_CHAT_AT = 0.8  # of the context size: suggest a new session
REPLY_ROOM = 4_096  # tokens kept free for the reply
IMAGE_TOKENS = 1_500  # a rough count for one page image
LONG_CHAT_NOTE = (
    "This chat is getting long for this model's memory. To keep it sharp, end the "
    "session and continue in a new one (the recap carries over)."
)
TRIMMED_NOTE = (
    "The oldest turns no longer fit in this model's memory, so it no longer sees them."
)
TOOL_FAILED_NOTE = "The model couldn't use a tool correctly, so its reply ends here."
NO_TOOLS_WARNING = "This model can't use tools: your progress won't be saved."
NOTE_TAG = "velocity-note"
NOT_NEEDED_KEY = "not-needed"  # local servers ignore the key, but the client needs one


# -- settings ------------------------------------------------------------------------


def unavailable_reason(config: Config | None) -> str | None:
    """Why the free engine can't run with these settings, or None if it can."""
    if config is None or not config.openai_base_url or not config.openai_model:
        return "Set up a free model in Settings first (a server and a model)."
    name = config.openai_api_key_env
    if name and not keys.free_key_env_ok(name):
        return (
            f"{name} can't hold the free model's key: in Settings, name a variable "
            "that ends in _API_KEY (not ANTHROPIC_API_KEY)."
        )
    if name and saved_key(config) is None:
        host = urlparse(config.openai_base_url).hostname
        return (
            f"No key for {host}: add it in Settings (it is saved in your system "
            f"keyring), or set {name} in the terminal that starts velocity."
        )
    return None


def saved_key(config: Config) -> str | None:
    """The server's key: the named environment variable, else the one saved in Settings
    for this server (the system keyring). A variable not meant for an API key is never
    read (keys.free_key_env_ok)."""
    env = config.openai_api_key_env
    return keys.get_key(
        keys.free_key_name(config.openai_base_url),
        env if keys.free_key_env_ok(env) else None,
    )


def api_key(config: Config) -> str:
    return saved_key(config) or NOT_NEEDED_KEY


def is_local(url: str | None) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host in ("localhost", "::1", "0.0.0.0") or host.startswith("127.")


def privacy_note(config: Config | None) -> str:
    """The page's reminder for this engine: where what the learner shares goes."""
    url = config.openai_base_url if config else None
    if is_local(url):
        return "This model runs on your computer: nothing you share leaves it."
    host = urlparse(url or "").hostname or "the model's server"
    return (
        f"What you share in a session, including any notes the tutor reads, is sent to "
        f"{host}. Free tiers may use conversations for training; check its terms."
    )


def make_client(config: Config) -> Any:
    import openai

    return openai.AsyncOpenAI(
        base_url=config.openai_base_url, api_key=api_key(config), max_retries=3
    )


async def check_model(config: Config, client: Any = None) -> dict:
    """The settings' Test: does the server answer, and can the model call a tool?
    Returns {"ok": bool, "tools": bool, "message": str}."""
    reason = unavailable_reason(config)
    if reason:
        return {"ok": False, "tools": False, "message": reason}
    client = client or make_client(config)
    tool = {
        "type": "function",
        "function": {
            "name": "check",
            "description": "Confirms that tools work. Call it with word set to 'ok'.",
            "parameters": {
                "type": "object",
                "properties": {"word": {"type": "string"}},
                "required": ["word"],
            },
        },
    }
    try:
        response = await client.chat.completions.create(
            model=config.openai_model,
            messages=[
                {"role": "user", "content": "Call the check tool with word 'ok'."}
            ],
            tools=[tool],
            timeout=180,
        )
    except Exception as e:  # noqa: BLE001 - reported to the learner as it is
        return {"ok": False, "tools": False, "message": api_error(e, config)}
    message = response.choices[0].message if response.choices else None
    calls = (message.tool_calls or []) if message else []
    tools = any(c.function.name == "check" for c in calls)
    text = (
        f"{config.openai_model} answered and used a tool."
        if tools
        else f"{config.openai_model} answered, but didn't use the tool. "
        + NO_TOOLS_WARNING
    )
    loaded = await ollama_context(config)
    if loaded is not None and loaded < config.openai_context_size:
        text += (
            f" But Ollama runs it with room for only {loaded:,} tokens, less than the "
            f"{config.openai_context_size:,} set here, and it cuts what doesn't fit "
            "without saying so (the tutor's instructions alone take about 5,000). Start "
            f"Ollama with OLLAMA_CONTEXT_LENGTH={config.openai_context_size}, or set a "
            "smaller context size here."
        )
    return {"ok": True, "tools": tools, "message": text}


async def ollama_context(config: Config) -> int | None:
    """The context size Ollama loaded the model with, if the server is Ollama (its
    default is small, and Ollama cuts longer prompts silently). None if unknown."""
    import httpx

    root = (config.openai_base_url or "").rstrip("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            running = (await client.get(f"{root}/api/ps")).json()
    except (httpx.HTTPError, ValueError):
        return None
    for model in running.get("models", []) if isinstance(running, dict) else []:
        wanted = config.openai_model
        names = (model.get("name"), model.get("model"))
        if wanted in names or (":" not in wanted and f"{wanted}:latest" in names):
            size = model.get("context_length")
            return size if isinstance(size, int) else None
    return None


def api_error(error: BaseException, config: Config | None = None) -> str:
    import openai

    where = (config.openai_base_url if config else None) or "the server"
    if isinstance(error, openai.APITimeoutError):
        return "The model took too long to answer. Try again."
    if isinstance(error, openai.APIConnectionError):
        return f"Nothing answered at {where}. Is the server running?"
    if isinstance(error, openai.AuthenticationError):
        return "The server refused the key. Check the key in your environment variable."
    if isinstance(error, openai.NotFoundError):
        model = config.openai_model if config else "the model"
        return f"The server doesn't know {model}, or the address is wrong."
    if isinstance(error, openai.RateLimitError):
        return (
            "Rate limited (free tiers allow only so many requests); wait and try again."
        )
    if isinstance(error, openai.APIStatusError):
        return f"Server error {error.status_code}: {error.message}"
    return f"The tutor failed: {error}"


# -- tools and messages in the OpenAI format -----------------------------------------


def openai_tools(tools: list[dict], images: bool) -> list[dict]:
    """Tool definitions (as agent.py makes them) for the chat completions API. Without
    images, `view_page` is left out: the model couldn't read what it returns."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in tools
        if images or t["name"] != "view_page"
    ]


def with_notes(text: str, notes: list[str]) -> str:
    """A learner message with velocity's notes for the tutor after it."""
    return "\n\n".join(
        [text, *(f"<{NOTE_TAG}>\n{n}\n</{NOTE_TAG}>" for n in notes if n)]
    )


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(
        b.get("text", "") for b in content if isinstance(b, dict) and "text" in b
    )


def convert_history(messages: list[dict]) -> list[dict]:
    """Messages in the Anthropic shape (history.rebuild_messages) -> OpenAI messages.
    System notes join the learner message before them; tool calls and results become
    `tool_calls` and `tool` messages."""
    out: list[dict] = []
    for m in messages:
        role, content = m["role"], m["content"]
        if role == "system":
            if out and out[-1]["role"] == "user":
                out[-1]["content"] = with_notes(out[-1]["content"], [content])
            continue
        if role == "user" and isinstance(content, list):
            for b in content:
                if b.get("type") == "tool_result":
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": b["tool_use_id"],
                            "content": _text_of(b.get("content", "")),
                        }
                    )
            continue
        if role == "assistant" and isinstance(content, list):
            text = "\n\n".join(
                b["text"] for b in content if b.get("type") == "text" and b["text"]
            )
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        "arguments": json.dumps(b["input"]),
                    },
                }
                for b in content
                if b.get("type") == "tool_use"
            ]
            message: dict = {"role": "assistant", "content": text or None}
            if calls:
                message["tool_calls"] = calls
            out.append(message)
            continue
        out.append({"role": role, "content": content})
    return out


def estimate_tokens(message: dict) -> int:
    """A rough token count (4 characters a token; a fixed amount per image)."""
    content = message.get("content")
    if isinstance(content, list):
        size = sum(
            IMAGE_TOKENS
            if p.get("type") == "image_url"
            else len(p.get("text", "")) // 4
            for p in content
        )
    else:
        size = len(content or "") // 4
    return size + len(json.dumps(message.get("tool_calls", ""))) // 4 + 4


def turn_starts(messages: list[dict]) -> list[int]:
    """Where each learner turn starts: a user message after an assistant's (or first).
    A user message after tool results carries a tool's images, not a new turn."""
    return [
        i
        for i, m in enumerate(messages)
        if m["role"] == "user" and (i == 0 or messages[i - 1]["role"] == "assistant")
    ]


class ThinkFilter:
    """Drops <think>...</think> from streamed text, also when a tag is split across
    chunks."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.inside = False
        self.buffer = ""

    def feed(self, text: str) -> str:
        self.buffer += text
        shown = ""
        while True:
            tag = self.CLOSE if self.inside else self.OPEN
            at = self.buffer.find(tag)
            if at == -1:
                # Keep what could be the start of a tag until the next chunk.
                keep = next(
                    (
                        n
                        for n in range(len(tag) - 1, 0, -1)
                        if self.buffer.endswith(tag[:n])
                    ),
                    0,
                )
                if not self.inside:
                    shown += self.buffer[: len(self.buffer) - keep]
                self.buffer = self.buffer[len(self.buffer) - keep :]
                return shown
            if not self.inside:
                shown += self.buffer[:at]
            self.buffer = self.buffer[at + len(tag) :]
            if self.inside:
                self.buffer = self.buffer.lstrip()
            self.inside = not self.inside

    def flush(self) -> str:
        rest, self.buffer = ("" if self.inside else self.buffer), ""
        return rest


# -- the loop ------------------------------------------------------------------------


@dataclass
class FreeAgent:
    """One chat's tutor on an OpenAI-compatible server. Like agent.TutorAgent, it owns the
    conversation, calls velocity's hook functions directly, and streams ChatEvents."""

    config: Config
    subject: Subject
    create: Callable[..., Any]  # client.chat.completions.create
    call_tool: ToolCaller
    tools: list[dict]  # OpenAI format
    skills: dict[str, Skill]
    model: str
    context_size: int = 32_768
    on_event: Callable[[ChatEvent], None] | None = None
    messages: list[dict] = field(default_factory=list)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    continues: str | None = None
    reopen: str | None = None
    web_ui: bool = True
    stop_requested: bool = False
    closed_empty: bool = False
    tool_log: list[tuple[str, dict]] = field(default_factory=list)
    _streamed: str = ""
    _pending_context: list[str] = field(default_factory=list)
    _warned_long: bool = False

    # -- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        """Open the session log; to resume, first rebuild the conversation."""
        if self.reopen:
            session = Session(
                self.subject.slug,
                self.reopen,
                record_dir(self.subject.slug),
                self.subject.sessions_dir,
            )
            names = {t["function"]["name"] for t in self.tools} | {"load_skill"}
            self.messages = convert_history(rebuild_messages(session, names))
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
                # In the terminal, velocity prints privacy_note() itself.
                "reminder_shown": True,
            },
        )
        self._pending_context.append(result["hookSpecificOutput"]["additionalContext"])

    def finish(self) -> None:
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

    @property
    def system(self) -> str:
        return system_prompt(self.subject, self.skills)

    @property
    def all_tools(self) -> list[dict]:
        return self.tools + [_LOAD_SKILL]

    # -- one learner turn --------------------------------------------------------------

    async def turn(self, text: str) -> str:
        """Handle one learner message; return the tutor's reply text (as logged)."""
        logged = hooks.user_prompt(
            self.config, self.subject, {"session_id": self.session_id, "prompt": text}
        )
        if logged.get("decision") == "block":
            self.finish()  # /end-session in an empty session
            self.closed_empty = True
            return ""
        if logged:
            self._pending_context.append(
                logged["hookSpecificOutput"]["additionalContext"]
            )
        invoked = parse_slash_command(text, self.skills)
        if invoked:
            skill, args = invoked
            self._pending_context.append(
                f"The learner invoked the skill `{skill.name}`"
                + (f" with arguments: {args}" if args else " without arguments")
                + f". Follow these instructions:\n\n{skill.instructions}"
            )
        contexts, self._pending_context = self._pending_context, []
        start = len(self.messages)
        self.messages.append({"role": "user", "content": with_notes(text, contexts)})

        self._streamed = ""
        self.stop_requested = False
        try:
            reply = await self._run_until_done()
        except asyncio.CancelledError:
            if not self.stop_requested:
                self._roll_back(start, contexts)
                raise
            asyncio.current_task().uncancel()
            reply = self._keep_stopped(start + 1)
        except BaseException:
            self._roll_back(start, contexts)
            raise
        hooks.stop(
            self.config,
            self.subject,
            {"session_id": self.session_id, "last_assistant_message": reply},
        )
        return reply

    def stop_reply(self) -> None:
        self.stop_requested = True

    def _keep_stopped(self, reply_start: int) -> str:
        del self.messages[reply_start:]
        partial = self._streamed.strip()
        self.messages.append(
            {
                "role": "assistant",
                "content": (partial + "\n\n" if partial else "")
                + "[The learner stopped this reply here.]",
            }
        )
        return (partial + "\n\n" if partial else "") + STOPPED_MARK

    def _roll_back(self, start: int, contexts: list[str]) -> None:
        del self.messages[start:]
        self._pending_context = contexts + self._pending_context

    async def _run_until_done(self) -> str:
        texts: list[str] = []
        bad_calls = 0
        for _ in range(MAX_ROUNDS):
            text, calls = await self._request()
            if text.strip():
                texts.append(text.strip())
            message: dict = {"role": "assistant", "content": text or None}
            if calls:
                message["tool_calls"] = calls
            self.messages.append(message)
            if not calls:
                return "\n\n".join(texts)
            images: list[dict] = []
            for call in calls:
                result, bad = await self._run_tool(call, images)
                bad_calls += bad
                self.messages.append(result)
            if images:
                self.messages.append(
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "The pages:"}, *images],
                    }
                )
            if bad_calls > MAX_TOOL_RETRIES:
                return self._give_up(texts, TOOL_FAILED_NOTE)
        return self._give_up(
            texts, "The model kept calling tools, so its reply ends here."
        )

    def _give_up(self, texts: list[str], note: str) -> str:
        """End a turn the model couldn't finish, with a note, keeping what it said."""
        self._emit(ChatEvent("note", text=note))
        self.messages.append({"role": "assistant", "content": f"[{note}]"})
        return "\n\n".join(texts)

    async def _request(self) -> tuple[str, list[dict]]:
        """One streamed request: the reply text (without thinking) and its tool calls."""
        self._fit_context()
        stream = await self.create(
            model=self.model,
            messages=[{"role": "system", "content": self.system}, *self.messages],
            tools=self.all_tools,
            stream=True,
        )
        think = ThinkFilter()
        text = ""
        calls: dict[int, dict] = {}
        started = False

        def show(chunk: str) -> None:
            nonlocal text, started
            if not chunk:
                return
            if not started:
                chunk = chunk.lstrip()
                if not chunk:
                    return
                started = True
            text += chunk
            self._streamed += chunk
            self._emit(ChatEvent("text", text=chunk))

        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                # Thinking in its own field (reasoning, reasoning_content) is skipped.
                show(think.feed(delta.content or ""))
                for d in delta.tool_calls or []:
                    call = calls.setdefault(
                        d.index,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    call["id"] = d.id or call["id"]
                    if d.function:
                        call["function"]["name"] += d.function.name or ""
                        call["function"]["arguments"] += d.function.arguments or ""
        finally:
            # Also when the learner stops the reply: hang up on the server.
            close = getattr(stream, "close", None) or getattr(stream, "aclose", None)
            if close:
                with contextlib.suppress(Exception):
                    await close()
        show(think.flush())
        if started:
            # Texts separated by tool calls are joined by a blank line in the log.
            self._emit(ChatEvent("text", text="\n\n"))
            self._streamed += "\n\n"
        ordered = [calls[i] for i in sorted(calls)]
        for n, call in enumerate(ordered):
            call["id"] = call["id"] or f"call_{len(self.messages)}_{n}"
        return text, ordered

    def _fit_context(self) -> None:
        """Suggest a new session near the context size; past it, leave out the oldest
        turns after the first."""
        fixed = (len(self.system) + len(json.dumps(self.all_tools))) // 4
        used = fixed + sum(estimate_tokens(m) for m in self.messages)
        if used > LONG_CHAT_AT * self.context_size and not self._warned_long:
            self._warned_long = True
            self._emit(ChatEvent("note", text=LONG_CHAT_NOTE))
        limit = self.context_size - REPLY_ROOM
        trimmed = False
        while used > limit:
            starts = turn_starts(self.messages)
            if len(starts) < 3:  # the first turn and the current one stay
                break
            dropped = self.messages[starts[1] : starts[2]]
            del self.messages[starts[1] : starts[2]]
            used -= sum(estimate_tokens(m) for m in dropped)
            trimmed = True
        if trimmed:
            self._emit(ChatEvent("note", text=TRIMMED_NOTE))

    async def _run_tool(self, call: dict, images: list[dict]) -> tuple[dict, int]:
        """Run one tool call; return its result message and 1 if the call was bad."""
        name = call["function"]["name"]
        raw = call["function"]["arguments"] or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict):
            return self._result(
                call,
                f"The arguments were not a JSON object: {raw[:200]}. Call the tool "
                "again with valid JSON.",
            ), 1
        known = {t["function"]["name"] for t in self.all_tools}
        if name not in known:
            return self._result(
                call, f"There is no tool named {name!r}. Tools: {', '.join(known)}."
            ), 1
        self.tool_log.append((name, args))
        self._emit(ChatEvent("tool", name=name, input=args))
        if name == "load_skill":
            skill = self.skills.get(str(args.get("name", "")))
            if skill is None:
                text, is_error = f"No skill named {args.get('name')!r}.", True
            else:
                text, is_error = skill.instructions, False
        else:
            content, is_error = await self.call_tool(name, args)
            text = _text_of(content)
            if isinstance(content, list):
                images += [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{b['source']['media_type']};base64,"
                            + b["source"]["data"]
                        },
                    }
                    for b in content
                    if b.get("type") == "image"
                ]
        hooks.tool_use(
            self.config,
            self.subject,
            {"tool_name": name, "tool_input": args, "is_error": is_error},
        )
        return self._result(call, ("Error: " if is_error else "") + text), 0

    @staticmethod
    def _result(call: dict, text: str) -> dict:
        return {"role": "tool", "tool_call_id": call["id"], "content": text}

    def _emit(self, event: ChatEvent) -> None:
        if self.on_event:
            self.on_event(event)


_LOAD_SKILL = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": "Load the full instructions of a skill by name (see the skills list).",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name, e.g. quiz"}
            },
            "required": ["name"],
        },
    },
}


# -- the terminal: velocity chat --engine openai (2026-11-11) ------------------------


async def chat(
    config: Config,
    subject: Subject,
    out: Any,
    read_line: Callable[[str], str],
    model: str | None = None,
    continues: str | None = None,
    reopen: str | None = None,
    client: Any = None,
    connect: Connector = connect_mcp,
) -> None:
    """`velocity chat` on the free engine: the same terminal chat as on the API, with the
    model from `--model` or Settings, and velocity's own privacy line instead of the
    tutor's reminder (which names Anthropic)."""
    if model:
        config = dataclasses.replace(config, openai_model=model)
    reason = unavailable_reason(config)
    if reason:
        raise EngineError(reason)
    client = client or make_client(config)

    def show(event: ChatEvent) -> None:
        if event.type == "text":
            out.write(event.text)
        elif event.type == "tool":
            args = ", ".join(f"{k}={v!r}"[:60] for k, v in (event.input or {}).items())
            out.write(f"\n  · {event.name}({args})\n")
        elif event.type == "note":
            out.write(f"\n[{event.text}]\n")
        out.flush()

    async with connect(subject) as (call_tool, tools):
        skills = load_skills()
        agent = FreeAgent(
            config=config,
            subject=subject,
            create=client.chat.completions.create,
            call_tool=call_tool,
            tools=openai_tools(
                [t for t in tools if t["name"] != "load_skill"], config.openai_images
            ),
            skills=skills,
            model=config.openai_model,
            context_size=config.openai_context_size,
            on_event=show,
            continues=continues,
            reopen=reopen,
            web_ui=False,
        )
        agent.start()
        # As web chats do, so --resume (and the page's Resume) default to this engine.
        Session(
            subject.slug,
            active_session_id(subject.slug),
            record_dir(subject.slug),
            subject.sessions_dir,
        ).append(
            "event", FREE, kind="engine", meta={"engine": FREE, "model": agent.model}
        )
        out.write(
            f"velocity: {subject.name} with {agent.model}, a free model (weaker).\n"
            f"{NOTE}\n{privacy_note(config)}\n"
            f"Log: {agent.log_path}\n"
            f"Skills: {', '.join('/' + s for s in skills)}. Exit with /exit or Ctrl+D.\n"
        )
        if reopen:
            session = Session(
                subject.slug, reopen, record_dir(subject.slug), subject.sessions_dir
            )
            out.write("\n" + resume_recap(session, cost_note=False))
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
                if line.strip() == "/model" or line.strip().startswith("/model "):
                    out.write(
                        f"This chat runs on {agent.model}. To use another free model, "
                        "start the chat with --model, or change it in Settings on the "
                        "web page.\n"
                    )
                    continue
                if message := unknown_command(line, skills):
                    out.write(message + "\n")
                    continue
                out.write("\n")
                try:
                    await agent.turn(line)
                except Exception as e:  # noqa: BLE001 - shown, and the chat goes on
                    out.write(f"\n[{api_error(e, config)}]\n")
                out.write("\n")
            if agent.ended and not agent.closed_empty:
                out.write("\nSession wrapped up.\n")
        finally:
            agent.finish()


# -- the web chat engine -------------------------------------------------------------


class FreeChat(ApiChat):
    """A web chat on the free engine: ApiChat's connection and turn handling, with a
    FreeAgent instead of the Anthropic loop, and no cost."""

    def __init__(
        self,
        config: Config,
        subject: Subject,
        reopen: str | None = None,
        continues: str | None = None,
        connect: Connector = connect_mcp,
        client: Any = None,
    ) -> None:
        self.config = config
        self.subject = subject
        self.model = config.openai_model or ""
        self.reopen = reopen
        self.continues = continues
        self.connect = connect
        self.client = client
        self.stream = None
        self.agent = None
        self.session_id = None
        self._closing = asyncio.Event()
        self._connection = None
        self._turn = asyncio.Lock()
        self._running_turn = None

    def _prepare(self) -> None:
        reason = unavailable_reason(self.config)
        if reason:
            raise EngineError(reason)
        if self.client is None:
            self.client = make_client(self.config)

    def _make_agent(self, call_tool: ToolCaller, tools: list) -> FreeAgent:
        return FreeAgent(
            config=self.config,
            subject=self.subject,
            create=self.client.chat.completions.create,
            call_tool=call_tool,
            # The MCP tools without load_skill, which FreeAgent adds itself.
            tools=openai_tools(
                [t for t in tools if t["name"] != "load_skill"],
                self.config.openai_images,
            ),
            skills=load_skills(),
            model=self.model,
            context_size=self.config.openai_context_size,
            continues=self.continues,
            reopen=self.reopen,
        )

    def _local_reply(self, text: str) -> str | None:
        command = text.strip().split(maxsplit=1)[0] if text.strip() else ""
        if command in ("/model", "/exit", "/quit"):
            return "Change the free model in Settings; end with End session or Pause."
        return unknown_command(text, self.agent.skills)

    def _error(self, error: BaseException) -> str:
        return api_error(error, self.config)

    def _cost(self) -> None:
        return None

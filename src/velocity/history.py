"""Rebuilding the own agent loop's message history from a session record.

The own loop resumes a paused session, whichever front end started it, by turning the
record back into API messages (decided 2026-11-09). The evals use the same builder to put
the earlier part of a case into the agent's history.

What the record keeps, and so what the tutor gets back:
- learner and tutor messages verbatim, and log annotations as the system note the tutor
  saw with the next learner message;
- blocks the tutor added to the log, as a note in its next message;
- tool calls with their input, but with a placeholder result: results can hold source
  text, which must not be copied into records. The tutor knows what it did, not what it
  read, and can read it again.
"""

from collections.abc import Iterable

from velocity.hooks import log_changes_context
from velocity.session import Session

RESULT_NOT_RECORDED = "(Result not recorded. The call succeeded.)"
ERROR_NOT_RECORDED = "(Result not recorded. The call failed.)"


def block_note(title: str | None, text: str) -> str:
    """How a block the tutor added to the log appears in its own message history."""
    return f"[Added to the session log: {title or 'note'}]\n{text}"


class HistoryBuilder:
    """Appends turns to a message list the way the agent loop would have built it."""

    def __init__(self, messages: list[dict] | None = None) -> None:
        self.messages = messages if messages is not None else []
        self._blocks: list[str] = []
        self._calls = 0

    def learner(self, text: str, context: Iterable[str] = ()) -> None:
        """A learner message, then the system notes that came with it."""
        self.messages.append({"role": "user", "content": text})
        self.messages += [{"role": "system", "content": c} for c in context if c]

    def block(self, text: str, title: str | None = None) -> None:
        self._blocks.append(block_note(title, text))

    def tool(self, name: str, args: dict, error: bool = False) -> None:
        call_id = f"replayed_{self._calls}"
        self._calls += 1
        self.messages.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": call_id, "name": name, "input": args}
                ],
            }
        )
        result = {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": ERROR_NOT_RECORDED if error else RESULT_NOT_RECORDED,
        }
        if error:
            result["is_error"] = True
        self.messages.append({"role": "user", "content": [result]})

    def tutor(self, text: str) -> None:
        text = "\n\n".join([*self._blocks, text])
        self._blocks = []
        last = self.messages[-1] if self.messages else None
        if last and last["role"] == "assistant" and isinstance(last["content"], str):
            last["content"] += "\n\n" + text
        else:
            self.messages.append({"role": "assistant", "content": text})


def rebuild_messages(session: Session, tool_names: set[str]) -> list[dict]:
    """The session's conversation so far as API messages, for resuming it on the own loop.

    Only calls to tools in `tool_names` are kept: a call to a tool the agent doesn't have
    would make the request invalid.
    """
    builder = HistoryBuilder()
    annotations: list[str] = []
    for e in sorted(session.events(), key=lambda e: e.ts):
        if e.role == "learner":
            context = [log_changes_context(annotations, [])] if annotations else []
            annotations = []
            builder.learner(e.text, context)
        elif e.role == "tutor":
            builder.tutor(e.text)
        elif e.role == "block":
            builder.block(e.text, e.title)
        elif e.kind == "tool" and e.text in tool_names:
            meta = e.meta or {}
            builder.tool(e.text, meta.get("input") or {}, bool(meta.get("error")))
        elif e.kind == "annotation":
            annotations.append(e.text)
    return builder.messages

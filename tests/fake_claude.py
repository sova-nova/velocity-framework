"""A stand-in for `claude -p` with stream-json, for testing web chat without Claude Code.

It speaks the protocol observed in the 2026-11-06 experiment and runs velocity's real
hooks at the same moments Claude Code does: SessionStart before the first reply,
UserPromptSubmit and Stop around each reply, SessionEnd when stdin closes. The subject
comes from FAKE_CLAUDE_SUBJECT; config and data directories from the usual XDG variables.

Messages it understands:
- "/end-session": records a recap (as the end_session tool would), so Stop wraps up.
- "use a tool": the reply includes a tool call.
- "crash": exits without a result.
Anything else gets the reply "You said: <message>", streamed in two pieces.
"""

import json
import os
import sys
import uuid

from velocity import hooks
from velocity.config import load_config, load_subject
from velocity.tools import TutorTools

args = sys.argv[1:]
resume = args[args.index("--resume") + 1] if "--resume" in args else None
session_id = resume or str(uuid.uuid4())
config = load_config()
subject = load_subject(config, os.environ["FAKE_CLAUDE_SUBJECT"])


def emit(event: dict) -> None:
    print(json.dumps({**event, "session_id": session_id}), flush=True)


def hook(name: str, payload: dict) -> None:
    getattr(hooks, name)(config, subject, {"session_id": session_id, **payload})


started = False
for line in sys.stdin:
    text = json.loads(line)["message"]["content"]
    if not started:
        hook("session_start", {"source": "resume" if resume else "startup"})
        started = True
    if text == "crash":
        sys.exit(1)
    hook("user_prompt", {"prompt": text})
    emit({"type": "system", "subtype": "init"})
    if text == "/end-session":
        TutorTools(config, subject).end_session("Covered: testing.")
        reply = "Session wrapped up."
    else:
        reply = f"You said: {text}"
    if text == "use a tool":
        emit(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__velocity__read_state",
                            "input": {},
                        }
                    ]
                },
            }
        )
    for piece in (reply[:5], reply[5:]):
        emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": piece},
                },
            }
        )
    emit(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}}
    )
    hook("stop", {"last_assistant_message": reply})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": reply})

if started:
    hook("session_end", {"reason": "other"})

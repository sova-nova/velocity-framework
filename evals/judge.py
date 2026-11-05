"""LLM judge: grades one replayed tutor turn against a fixed teaching rubric.

Each criterion gets "pass", "fail" or "n/a" with a short reason (written first, so the
verdict follows from it). The case's own `judge` note is graded as one more criterion,
"focus". The judge sees the same history the tutor saw, the tool calls it made, and its
reply.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from evals.cases import Case, ReplayInput, Turn
from evals.replay import block_note
from velocity.agent import FALLBACK_BETA, FALLBACK_MODELS, Usage

# criterion id -> (short name, what passing means)
RUBRIC: dict[str, tuple[str, str]] = {
    "asks_before_telling": (
        "Asks before telling",
        (
            "Draws out the learner's thinking before explaining: asks what they know or lets "
            "them try first, where that fits. Fail if it explains something the learner could "
            "usefully have been asked first. N/A if the learner only asked a factual or "
            "practical question that should simply be answered."
        ),
    ),
    "hints_before_answers": (
        "Hints before answers",
        (
            "Does not give away an answer or step the learner is working toward; gives a hint "
            "or a smaller step instead. N/A if no problem or question is in progress."
        ),
    ),
    "correct": (
        "Correct",
        (
            "Everything it states about the subject is correct: math, facts, notation, and "
            "citations. Fail on any error."
        ),
    ),
    "honest_feedback": (
        "Honest feedback",
        (
            "Responds to the learner's answer or claim accurately: confirms what is right, "
            "clearly corrects what is wrong, never agrees with an error to be agreeable, and "
            "admits its own mistakes. N/A if the learner made no answer or claim."
        ),
    ),
    "clear_next_step": (
        "Clear next step",
        (
            "Ends with one clear thing for the learner to do or answer next, not several "
            "unrelated questions. N/A if the session is ending."
        ),
    ),
    "focus": (
        "Case focus",
        "Does what this case specifically checks for (given below as the case focus).",
    ),
}
VERDICTS = ("pass", "fail", "n/a")

JUDGE_SYSTEM = """\
You grade one turn of an AI tutor. The tutor teaches one learner one-on-one. Its principles:
diagnose what the learner knows before teaching; ask before telling; give hints before
answers; correct errors honestly, including its own, never agreeing to be agreeable; cite
the learner's sources when it has them; keep the learner doing the thinking.

You get the conversation so far, the learner's new message, the tools the tutor called
during its turn, and the tutor's reply. Grade only the reply (and its tool calls) against
each criterion. For each, write a one- or two-sentence reason first, then the verdict:
"pass", "fail", or "n/a" when the criterion doesn't apply to this turn. Be strict but fair:
fail only for a real shortcoming a good human tutor would avoid, not for style preferences.
"""

# An async callable like `client.beta.messages.create(**params)`.
CreateFn = Callable[..., Awaitable[Any]]


@dataclass
class JudgeResult:
    verdicts: dict[str, dict[str, str]]  # criterion -> {"reason": ..., "verdict": ...}
    usage: Usage
    model: str


def judge_schema() -> dict:
    item = {
        "type": "object",
        "properties": {
            "reason": {"type": "string"},
            "verdict": {"type": "string", "enum": list(VERDICTS)},
        },
        "required": ["reason", "verdict"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {c: item for c in RUBRIC},
        "required": list(RUBRIC),
        "additionalProperties": False,
    }


def render_history(history: tuple[Turn, ...]) -> str:
    lines = []
    for t in history:
        if t.role == "learner":
            lines.append(f"LEARNER: {t.text}")
        elif t.role == "tutor":
            lines.append(f"TUTOR: {t.text}")
        elif t.role == "block":
            lines.append(f"TUTOR {block_note(t)}")
        elif t.role == "annotation":
            lines.append(f"[The learner typed into the session log:] {t.text}")
    return "\n\n".join(lines) or "(This is the first message of the session.)"


def judge_prompt(
    case: Case, inp: ReplayInput, reply: str, tool_log: list[tuple[str, dict]]
) -> str:
    sources = ", ".join(inp.sources) or "none (it teaches from general knowledge)"
    annotations = "".join(
        f"\n[Just before this, the learner typed into the session log:] {a}"
        for a in inp.annotations
    )
    tools = (
        "\n".join(
            f"- {name}({json.dumps(args, ensure_ascii=False)})"
            for name, args in tool_log
        )
        or "(none)"
    )
    rubric = "\n".join(f"- {c} ({name}): {text}" for c, (name, text) in RUBRIC.items())
    return f"""\
Subject: {inp.subject_name}. Sources: {sources}.

<conversation_so_far>
{render_history(inp.history)}
</conversation_so_far>

<learner_message>{annotations}
{inp.message}
</learner_message>

<tutor_tool_calls>
{tools}
</tutor_tool_calls>

<tutor_reply>
{reply}
</tutor_reply>

Case focus: {case.judge or "(none given; grade focus as n/a)"}

Criteria:
{rubric}
"""


async def judge(
    create: CreateFn,
    case: Case,
    inp: ReplayInput,
    reply: str,
    tool_log: list[tuple[str, dict]],
    model: str,
) -> JudgeResult:
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": 16_000,
        "system": JUDGE_SYSTEM,
        "messages": [
            {"role": "user", "content": judge_prompt(case, inp, reply, tool_log)}
        ],
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": judge_schema()},
        },
    }
    if model in FALLBACK_MODELS:
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    response = await create(**params)
    usage = Usage()
    usage.add(response.usage)
    if response.stop_reason == "refusal":
        raise RuntimeError("the judge declined to grade this turn")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("the judge's answer was cut off (max_tokens)")
    text = next(b.text for b in response.content if b.type == "text")
    verdicts = json.loads(text)
    return JudgeResult(verdicts, usage, model)

"""Hand labels for the judge: export replies to label, then measure agreement.

    uv run python -m evals.label export --run evals/runs/<run>.json --n 15
    uv run python -m evals.label agree --labels evals/labels/<file>.md

`export` writes a Markdown file (readable in Obsidian) with one section per sampled reply:
the conversation so far, the learner's message, the tutor's tool calls and reply, and a
line per rubric criterion to fill in with pass, fail or n/a. It doesn't show the judge's
verdicts or which model wrote the reply, so the labels stay independent.

`agree` compares the filled-in labels with the judge's verdicts from the same run:
agreement and Cohen's kappa per criterion, and every disagreement with the judge's reason.
"""

import argparse
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from evals.cases import EVALS_DIR, load_cases, replay_input
from evals.judge import RUBRIC, VERDICTS
from evals.replay import block_note

LABELS_DIR = EVALS_DIR / "labels"
ITEM = re.compile(r"<!-- item (\{.*?\}) -->")
LABEL_LINE = re.compile(r"^- .*\((\w+)\):[ \t]*(.*?)\s*$", re.MULTILINE)
ALIASES = {"pass": "pass", "fail": "fail", "n/a": "n/a", "na": "n/a", "-": "n/a"}


def sample(outcomes: list[dict], n: int, seed: int) -> list[dict]:
    """n graded replies, alternating between models, in a fixed random order."""
    rng = random.Random(seed)
    by_model: dict[str, list[dict]] = {}
    for o in outcomes:
        if o["status"] == "ok" and o.get("judge"):
            by_model.setdefault(o["model"], []).append(o)
    for items in by_model.values():
        rng.shuffle(items)
    picked: list[dict] = []
    while len(picked) < n and any(by_model.values()):
        for items in by_model.values():
            if items and len(picked) < n:
                picked.append(items.pop())
    rng.shuffle(picked)
    return picked


def quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.strip().splitlines())


def render_item(index: int, total: int, outcome: dict, cases: dict) -> str:
    case = cases[outcome["case"]]
    inp = replay_input(case)
    turns = []
    for t in inp.history:
        who = {
            "learner": "**Learner:**",
            "tutor": "**Tutor:**",
            "annotation": "**Learner (typed into the log):**",
            "block": "**Tutor (added to the log):**",
            "tool": "**Tutor (called a tool):**",
        }[t.role]
        if t.role == "tool":
            args = ", ".join(f"{k}={v!r}" for k, v in (t.args or {}).items())
            turns.append(f"{who} `{t.text}({args})`")
        else:
            turns.append(f"{who} {block_note(t) if t.role == 'block' else t.text}")
    history = "\n\n".join(turns) or "(This is the first message of the session.)"
    annotations = "".join(
        f"**Learner (typed into the log just before):** {a}\n\n"
        for a in inp.annotations
    )
    tools = ", ".join(
        f"`{name}({', '.join(f'{k}={v!r}' for k, v in args.items())})`"
        for name, args in outcome["tool_calls"]
    )
    labels = "\n".join(f"- {name} ({c}): _" for c, (name, _) in RUBRIC.items())
    return f"""\
## Reply {index} of {total}
<!-- item {json.dumps({"case": outcome["case"], "model": outcome["model"]})} -->

**Case:** {case.about}

**Case focus:** {case.judge or "(none: mark focus n/a)"}

**Subject:** {inp.subject_name}, sources: {", ".join(inp.sources) or "none"}

> [!note]- Conversation so far (click to expand)
{quote(history)}

{annotations}**Learner's message:** {inp.message}

**Tutor's tool calls:** {tools or "none"}

**Tutor's reply:**

{quote(outcome["reply"])}

**Your labels** (pass, fail, or n/a):
{labels}
- Notes (optional):
"""


def export(run_path: Path, n: int, seed: int, out: Path) -> Path:
    run = json.loads(run_path.read_text(encoding="utf-8"))
    cases = {c.id: c for c in load_cases()}
    items = sample(run["outcomes"], n, seed)
    if not items:
        raise SystemExit(f"{run_path} has no judged replies to label")
    criteria = "\n".join(f"- **{name}**: {text}" for name, text in RUBRIC.values())
    header = f"""\
# Eval labels

<!-- run {json.dumps({"run": run_path.as_posix()})} -->
{len(items)} tutor replies from one eval run, in random order. The model behind each
reply and the judge's verdicts are hidden.

**How to label:** read the case, the conversation so far and the tutor's reply. Then
replace each `_` with **pass**, **fail**, or **n/a** (the criterion doesn't apply to
this turn). Judge the reply as a strict but fair human tutor would: fail only for a real
shortcoming, not for style. "Case focus" is what that particular case checks for.

**Criteria:**
{criteria}

---

"""
    body = "\n---\n\n".join(
        render_item(i, len(items), o, cases) for i, o in enumerate(items, 1)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + body, encoding="utf-8")
    return out


@dataclass(frozen=True)
class Labeled:
    case: str
    model: str
    labels: dict[str, str]  # criterion -> verdict (missing if not filled in)


def parse_labels(text: str) -> tuple[str | None, list[Labeled]]:
    run = re.search(r"<!-- run (\{.*?\}) -->", text)
    run_path = json.loads(run.group(1))["run"] if run else None
    parts = ITEM.split(text)
    items = []
    for meta, body in zip(parts[1::2], parts[2::2]):
        meta = json.loads(meta)
        section = body.split("**Your labels**", 1)[-1]
        labels = {}
        for criterion, value in LABEL_LINE.findall(section):
            value = ALIASES.get(value.strip().strip("*").lower())
            if criterion in RUBRIC and value:
                labels[criterion] = value
        items.append(Labeled(meta["case"], meta["model"], labels))
    return run_path, items


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    expected = sum(
        (sum(a == v for a, _ in pairs) / n) * (sum(b == v for _, b in pairs) / n)
        for v in VERDICTS
    )
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def agreement(items: list[Labeled], outcomes: list[dict]) -> str:
    judged = {(o["case"], o["model"]): o.get("judge", {}) for o in outcomes}
    rows = [
        "| Criterion | Labeled | Agree | Agreement | Kappa |",
        "|---|---|---|---|---|",
    ]
    disagreements = []
    all_pairs = []
    by_model: dict[str, list[tuple[str, str]]] = {}
    for criterion, (name, _) in RUBRIC.items():
        pairs = []
        for item in items:
            mine = item.labels.get(criterion)
            theirs = judged.get((item.case, item.model), {}).get(criterion)
            if mine and theirs:
                pairs.append((mine, theirs["verdict"]))
                by_model.setdefault(item.model, []).append((mine, theirs["verdict"]))
                if mine != theirs["verdict"]:
                    disagreements.append(
                        f"- {item.case} [{item.model}] {criterion}: you {mine}, "
                        f"judge {theirs['verdict']} ({theirs['reason']})"
                    )
        all_pairs += pairs
        rows.append(row(name, pairs))
    rows.append(row("**All**", all_pairs))
    # The judge may favor replies from its own model; compare agreement per model.
    rows += [row(f"All, {model}", pairs) for model, pairs in sorted(by_model.items())]
    out = "\n".join(rows)
    if disagreements:
        out += "\n\nDisagreements:\n" + "\n".join(disagreements)
    return out


def row(name: str, pairs: list[tuple[str, str]]) -> str:
    agree = sum(a == b for a, b in pairs)
    kappa = cohen_kappa(pairs)
    pct = f"{agree / len(pairs):.0%}" if pairs else "-"
    return (
        f"| {name} | {len(pairs)} | {agree} | {pct} | "
        f"{'-' if kappa is None else f'{kappa:.2f}'} |"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.label")
    commands = parser.add_subparsers(dest="command", required=True)
    exp = commands.add_parser("export", help="write a labeling file from a run")
    exp.add_argument("--run", type=Path, required=True)
    exp.add_argument("--n", type=int, default=15)
    exp.add_argument("--seed", type=int, default=1)
    exp.add_argument("--out", type=Path, default=LABELS_DIR / "labels.md")
    agr = commands.add_parser("agree", help="compare your labels with the judge")
    agr.add_argument("--labels", type=Path, required=True)
    agr.add_argument("--run", type=Path, help="defaults to the run named in the file")
    args = parser.parse_args(argv)

    if args.command == "export":
        print(f"Wrote {export(args.run, args.n, args.seed, args.out)}")
        return 0
    run_path, items = parse_labels(args.labels.read_text(encoding="utf-8"))
    run = args.run or Path(run_path or "")
    outcomes = json.loads(run.read_text(encoding="utf-8"))["outcomes"]
    missing = sum(len(RUBRIC) - len(i.labels) for i in items)
    if missing:
        print(f"Note: {missing} labels are not filled in; they are left out.\n")
    print(agreement(items, outcomes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

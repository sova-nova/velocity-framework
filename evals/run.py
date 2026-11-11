"""Run the eval cases against one or more models; report code checks and judge verdicts.

    uv run python -m evals.run --models opus,sonnet
    uv run python -m evals.run --cases '2026-11-05-1-*' --models sonnet --budget 1
    uv run python -m evals.run --judge none        # code checks only (cheaper)
    uv run python -m evals.run --models free:qwen3:4b,free:qwen3:8b --judge none

Claude models and the judge need ANTHROPIC_API_KEY. `free:<model>` runs a model on an
OpenAI-compatible server (default: Ollama on this computer, --free-url to change) on the
free engine's loop; that costs nothing, and its cases run one at a time. Each case runs one tutor turn in a throwaway vault. Cases that use
a source need its PDF: by default the paths come from your velocity config (the subject
with the same slug), or pass --source Stewart=/path/to/book.pdf. The OCR cache is reused
read-only; eval runs never run OCR. Results are written to evals/runs/ (not committed).
"""

import argparse
import asyncio
import fnmatch
import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from evals.cases import EVALS_DIR, Case, load_cases, replay_input
from evals.checks import CheckResult, run_checks
from evals.judge import RUBRIC, judge
from evals.replay import ReplayResult, SkipCase, replay
from velocity.agent import DEFAULT_EFFORT, resolve_model
from velocity.config import ConfigError, data_dir, load_config, load_subject
from velocity.pdf_source import default_cache_dir

RUNS_DIR = EVALS_DIR / "runs"
# A model on an OpenAI-compatible server, run on the free engine's loop: "free:qwen3:4b".
FREE_PREFIX = "free:"


def model_id(name: str) -> str:
    """A Claude alias or id -> its full id; a free model stays as given."""
    return name if name.startswith(FREE_PREFIX) else resolve_model(name)


@dataclass
class Outcome:
    case: str
    model: str
    status: str  # "ok", "skipped", "error"
    checks: list[CheckResult] = field(default_factory=list)
    reply: str = ""
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    cost: float = 0.0  # the tutor's turn
    detail: str = ""
    judge: dict[str, dict[str, str]] = field(default_factory=dict)
    judge_cost: float = 0.0
    judge_error: str = ""

    @property
    def passed(self) -> bool:
        """All code checks passed (judge verdicts are reported separately)."""
        return self.status == "ok" and all(c.passed for c in self.checks)

    def verdicts(self, verdict: str) -> list[str]:
        return [c for c, v in self.judge.items() if v["verdict"] == verdict]


def select_cases(
    cases: list[Case], patterns: list[str] | None, limit: int | None
) -> list[Case]:
    if patterns:
        cases = [c for c in cases if any(fnmatch.fnmatch(c.id, p) for p in patterns)]
    return cases[:limit] if limit else cases


def default_sources(slugs: set[str]) -> dict[str, Path]:
    """Source name -> PDF path, from the user's velocity config, where available."""
    sources: dict[str, Path] = {}
    try:
        config = load_config()
    except ConfigError:
        return sources
    for slug in slugs:
        try:
            subject = load_subject(config, slug)
        except ConfigError:
            continue
        sources.update({s.name: s.path for s in subject.sources if s.path.exists()})
    return sources


def link_ocr_cache(
    sources: dict[str, Path], real_data: Path, sandbox_data: Path
) -> None:
    """Make the real OCR cache visible inside the sandbox data dir."""
    for pdf in sources.values():
        real = real_data / "sources" / default_cache_dir(pdf).name
        if real.is_dir():
            target = sandbox_data / "velocity" / "sources" / real.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.symlink_to(real, target_is_directory=True)


def cost_of(result: ReplayResult) -> float:
    costs = [u.cost(m) for m, u in result.usage.models.items()]
    return sum(c for c in costs if c is not None)


async def run_all(
    cases: list[Case],
    models: list[str],
    *,
    stream,
    sources: dict[str, Path],
    sandbox: Path,
    budget: float,
    concurrency: int,
    effort: str,
    create=None,  # like client.beta.messages.create, for the judge
    judge_model: str | None = None,
    report=print,
    free_create=None,  # an OpenAI-compatible client's chat.completions.create
    free_context: int = 32_768,
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    spent = 0.0
    gate = asyncio.Semaphore(concurrency)
    # A model on this computer answers one request at a time; queueing more only makes
    # each one wait (and time out).
    free_gate = asyncio.Semaphore(1)

    async def one(case: Case, model: str) -> None:
        nonlocal spent
        free = model.startswith(FREE_PREFIX)
        async with free_gate if free else gate:
            if spent >= budget:
                outcomes.append(
                    Outcome(case.id, model, "skipped", detail="budget reached")
                )
                return
            inp = replay_input(case)
            try:
                result = await replay(
                    inp,
                    sandbox=sandbox / f"{case.id}-{model.replace(':', '_')}",
                    stream=stream,
                    model=model.removeprefix(FREE_PREFIX),
                    effort=effort,
                    source_paths=sources,
                    free_create=free_create if free else None,
                    free_context=free_context,
                )
            except SkipCase as e:
                outcome = Outcome(case.id, model, "skipped", detail=str(e))
            except Exception as e:  # noqa: BLE001 - one failing case must not stop the run
                e = root_cause(e)
                outcome = Outcome(
                    case.id, model, "error", detail=f"{type(e).__name__}: {e}"
                )
            else:
                cost = cost_of(result)
                spent += cost
                outcome = Outcome(
                    case.id,
                    model,
                    "ok",
                    run_checks(case.expect, result.reply, result.tool_log),
                    result.reply,
                    result.tool_log,
                    cost,
                )
                if judge_model:
                    try:
                        graded = await judge(
                            create,
                            case,
                            inp,
                            result.reply,
                            result.tool_log,
                            judge_model,
                        )
                    except Exception as e:  # noqa: BLE001
                        e = root_cause(e)
                        outcome.judge_error = f"{type(e).__name__}: {e}"
                    else:
                        outcome.judge = graded.verdicts
                        outcome.judge_cost = graded.usage.cost(judge_model) or 0.0
                        spent += outcome.judge_cost
            outcomes.append(outcome)
            report(format_outcome(outcome))

    await asyncio.gather(*(one(c, m) for m in models for c in cases))
    order = {
        (c.id, m): i for i, (m, c) in enumerate((m, c) for m in models for c in cases)
    }
    return sorted(outcomes, key=lambda o: order[(o.case, o.model)])


def root_cause(e: BaseException) -> BaseException:
    """The real error inside the exception groups that MCP's task groups wrap it in."""
    while isinstance(e, BaseExceptionGroup) and len(e.exceptions) == 1:
        e = e.exceptions[0]
    return e


def format_outcome(o: Outcome) -> str:
    if o.status != "ok":
        return f"{o.status.upper():5} {o.case} [{o.model}] {o.detail}"
    failed = [c for c in o.checks if not c.passed]
    verdict = "PASS" if o.passed else "FAIL"
    passed = len(o.checks) - len(failed)
    lines = [
        f"{verdict:5} {o.case} [{o.model}] {passed}/{len(o.checks)} checks, ${o.cost:.3f}"
    ]
    lines += [
        f"        x {c.name}" + (f" ({c.detail})" if c.detail else "") for c in failed
    ]
    if o.judge:
        lines[0] += (
            f"; judge {len(o.verdicts('pass'))} pass, {len(o.verdicts('fail'))} fail, "
            f"${o.judge_cost:.3f}"
        )
        lines += [
            f"        judge fail {c}: {o.judge[c]['reason']}"
            for c in o.verdicts("fail")
        ]
    if o.judge_error:
        lines.append(f"        judge error: {o.judge_error}")
    return "\n".join(lines)


def summary(outcomes: list[Outcome], models: list[str]) -> str:
    rows = [
        (
            "| Model | Cases passing all checks | Checks passed | Judge: criteria passed "
            "| Skipped | Errors | Tutor cost | Judge cost |"
        ),
        "|---|---|---|---|---|---|---|---|",
    ]
    for model in models:
        mine = [o for o in outcomes if o.model == model]
        ok = [o for o in mine if o.status == "ok"]
        checks = [c for o in ok for c in o.checks]
        passes = sum(len(o.verdicts("pass")) for o in ok)
        graded = passes + sum(len(o.verdicts("fail")) for o in ok)
        rows.append(
            f"| {model} | {sum(o.passed for o in ok)}/{len(ok)} "
            f"| {sum(c.passed for c in checks)}/{len(checks)} "
            f"| {passes}/{graded} "
            f"| {sum(o.status == 'skipped' for o in mine)} "
            f"| {sum(o.status == 'error' for o in mine)} "
            f"| ${sum(o.cost for o in mine):.2f} "
            f"| ${sum(o.judge_cost for o in mine):.2f} |"
        )
    return "\n".join(rows)


def judge_table(outcomes: list[Outcome], models: list[str]) -> str:
    """Judge verdicts per criterion and model: passed / graded (n/a not counted)."""
    rows = [
        "| Criterion | " + " | ".join(models) + " |",
        "|---" * (len(models) + 1) + "|",
    ]
    for criterion, (name, _) in RUBRIC.items():
        cells = []
        for model in models:
            verdicts = [
                o.judge[criterion]["verdict"]
                for o in outcomes
                if o.model == model and criterion in o.judge
            ]
            graded = [v for v in verdicts if v != "n/a"]
            cells.append(f"{graded.count('pass')}/{len(graded)}")
        rows.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run", description=__doc__.split("\n")[0]
    )
    parser.add_argument(
        "--models", default="opus,sonnet", help="comma-separated, e.g. opus,sonnet"
    )
    parser.add_argument(
        "--cases", help="comma-separated case ids or patterns, e.g. 's2-*'"
    )
    parser.add_argument("--limit", type=int, help="run only the first N selected cases")
    parser.add_argument(
        "--budget", type=float, default=5.0, help="stop starting cases after $N"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    parser.add_argument(
        "--judge", default="opus", help="judge model, or 'none' for code checks only"
    )
    parser.add_argument("--source", action="append", default=[], help="NAME=PDF path")
    parser.add_argument(
        "--free-url",
        default="http://localhost:11434/v1",
        help="server for free: models (default: Ollama on this computer)",
    )
    parser.add_argument(
        "--free-key-env", help="environment variable with the free server's key"
    )
    parser.add_argument(
        "--free-context",
        type=int,
        default=32_768,
        help="context size of free: models (match the server's setting)",
    )
    parser.add_argument("--list", action="store_true", help="list the cases and exit")
    args = parser.parse_args(argv)

    cases = select_cases(
        load_cases(), args.cases.split(",") if args.cases else None, args.limit
    )
    if args.list:
        print("\n".join(f"{c.id}: {c.about}" for c in cases))
        return 0
    if not cases:
        print("No cases selected.", file=sys.stderr)
        return 1
    models = [model_id(m) for m in args.models.split(",")]
    sources = default_sources({replay_input(c).subject_slug for c in cases})
    for spec in args.source:
        name, _, path = spec.partition("=")
        sources[name] = Path(path).expanduser()

    import anthropic
    import openai

    # The in-process MCP server turns on INFO logging; keep the output to the results.
    for name in ("httpx", "mcp", "anthropic", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    judge_model = None if args.judge == "none" else resolve_model(args.judge)
    # Only runs that use Claude need a key (free models with --judge none don't).
    needs_claude = judge_model or any(not m.startswith(FREE_PREFIX) for m in models)
    client = anthropic.AsyncAnthropic() if needs_claude else None
    free = openai.AsyncOpenAI(
        base_url=args.free_url,
        api_key=(os.environ.get(args.free_key_env) if args.free_key_env else None)
        or "not-needed",
        timeout=900,  # a model on this computer can be slow
    )
    real_data = data_dir()
    with tempfile.TemporaryDirectory(prefix="velocity-evals-") as tmp:
        sandbox = Path(tmp)
        os.environ["XDG_DATA_HOME"] = str(sandbox / "data")
        link_ocr_cache(sources, real_data, sandbox / "data")
        outcomes = asyncio.run(
            run_all(
                cases,
                models,
                stream=client.beta.messages.stream if client else None,
                sources=sources,
                sandbox=sandbox,
                budget=args.budget,
                concurrency=args.concurrency,
                effort=args.effort,
                create=client.beta.messages.create if client else None,
                judge_model=judge_model,
                free_create=free.chat.completions.create,
                free_context=args.free_context,
            )
        )

    print("\n" + summary(outcomes, models))
    if judge_model:
        print("\n" + judge_table(outcomes, models))
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(
        json.dumps(
            {
                "models": models,
                "effort": args.effort,
                "judge_model": judge_model,
                "outcomes": [asdict(o) for o in outcomes],
            },
            indent=1,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nFull results: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

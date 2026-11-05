"""Run the eval cases against one or more models and report the code checks.

    uv run python -m evals.run --models opus,sonnet
    uv run python -m evals.run --cases s1-turn1,s2-socratic --models sonnet --budget 1

Needs ANTHROPIC_API_KEY. Each case runs one tutor turn in a throwaway vault. Cases that use
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
from evals.replay import ReplayResult, SkipCase, replay
from velocity.agent import DEFAULT_EFFORT, resolve_model
from velocity.config import ConfigError, data_dir, load_config, load_subject
from velocity.pdf_source import default_cache_dir

RUNS_DIR = EVALS_DIR / "runs"


@dataclass
class Outcome:
    case: str
    model: str
    status: str  # "ok", "skipped", "error"
    checks: list[CheckResult] = field(default_factory=list)
    reply: str = ""
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    cost: float = 0.0
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "ok" and all(c.passed for c in self.checks)


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
    report=print,
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    spent = 0.0
    gate = asyncio.Semaphore(concurrency)

    async def one(case: Case, model: str) -> None:
        nonlocal spent
        async with gate:
            if spent >= budget:
                outcomes.append(
                    Outcome(case.id, model, "skipped", detail="budget reached")
                )
                return
            try:
                result = await replay(
                    replay_input(case),
                    sandbox=sandbox / f"{case.id}-{model}",
                    stream=stream,
                    model=model,
                    effort=effort,
                    source_paths=sources,
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
    return "\n".join(lines)


def summary(outcomes: list[Outcome], models: list[str]) -> str:
    rows = [
        "| Model | Cases passed | Checks passed | Skipped | Errors | Cost |",
        "|---|---|---|---|---|---|",
    ]
    for model in models:
        mine = [o for o in outcomes if o.model == model]
        ok = [o for o in mine if o.status == "ok"]
        checks = [c for o in ok for c in o.checks]
        rows.append(
            f"| {model} | {sum(o.passed for o in ok)}/{len(ok)} "
            f"| {sum(c.passed for c in checks)}/{len(checks)} "
            f"| {sum(o.status == 'skipped' for o in mine)} "
            f"| {sum(o.status == 'error' for o in mine)} "
            f"| ${sum(o.cost for o in mine):.2f} |"
        )
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
    parser.add_argument("--source", action="append", default=[], help="NAME=PDF path")
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
    models = [resolve_model(m) for m in args.models.split(",")]
    sources = default_sources({replay_input(c).subject_slug for c in cases})
    for spec in args.source:
        name, _, path = spec.partition("=")
        sources[name] = Path(path).expanduser()

    import anthropic

    # The in-process MCP server turns on INFO logging; keep the output to the results.
    for name in ("httpx", "mcp", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)
    stream = anthropic.AsyncAnthropic().beta.messages.stream
    real_data = data_dir()
    with tempfile.TemporaryDirectory(prefix="velocity-evals-") as tmp:
        sandbox = Path(tmp)
        os.environ["XDG_DATA_HOME"] = str(sandbox / "data")
        link_ocr_cache(sources, real_data, sandbox / "data")
        outcomes = asyncio.run(
            run_all(
                cases,
                models,
                stream=stream,
                sources=sources,
                sandbox=sandbox,
                budget=args.budget,
                concurrency=args.concurrency,
                effort=args.effort,
            )
        )

    print("\n" + summary(outcomes, models))
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(
        json.dumps(
            {
                "models": models,
                "effort": args.effort,
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

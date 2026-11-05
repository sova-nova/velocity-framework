"""Deterministic checks on one replayed tutor turn.

A case's "expect" object can contain:

- "calls": tools that must be called, e.g. ["record_question"]. "load_skill:quiz" means
  load_skill with name "quiz".
- "calls_any": groups of tools of which at least one must be called, e.g.
  [["list_sources", "get_outline"]].
- "not_calls": tools that must not be called.
- "mentions": {label: regex} the reply must match (case-insensitive).
- "not_mentions": {label: regex} the reply must not match (case-insensitive).
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def _called(spec: str, tool_log: list[tuple[str, dict]]) -> bool:
    name, _, skill = spec.partition(":")
    return any(
        tool == name and (not skill or args.get("name") == skill)
        for tool, args in tool_log
    )


def run_checks(
    expect: dict, reply: str, tool_log: list[tuple[str, dict]]
) -> list[CheckResult]:
    unknown = set(expect) - {
        "calls",
        "calls_any",
        "not_calls",
        "mentions",
        "not_mentions",
    }
    if unknown:
        raise ValueError(f"unknown expectations: {', '.join(sorted(unknown))}")
    called = ", ".join(t for t, _ in tool_log) or "no tools"
    results = []
    for spec in expect.get("calls", []):
        results.append(CheckResult(f"calls {spec}", _called(spec, tool_log), called))
    for group in expect.get("calls_any", []):
        results.append(
            CheckResult(
                f"calls one of {', '.join(group)}",
                any(_called(spec, tool_log) for spec in group),
                called,
            )
        )
    for spec in expect.get("not_calls", []):
        results.append(
            CheckResult(f"does not call {spec}", not _called(spec, tool_log), called)
        )
    for label, pattern in expect.get("mentions", {}).items():
        match = re.search(pattern, reply, re.IGNORECASE)
        results.append(CheckResult(f"mentions {label}", match is not None))
    for label, pattern in expect.get("not_mentions", {}).items():
        match = re.search(pattern, reply, re.IGNORECASE)
        results.append(
            CheckResult(
                f"does not mention {label}",
                match is None,
                f"found {match.group(0)!r}" if match else "",
            )
        )
    return results

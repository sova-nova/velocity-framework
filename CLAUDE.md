# CLAUDE.md

## Project

`velocity`: a local, open-source subject tutor built on an LLM. A learner picks a subject; the tutor teaches, questions, and quizzes, optionally grounded in learner-supplied sources (e.g. a textbook PDF). It keeps per-subject session logs (rendered in Obsidian) and learner state, supports learner-owned note-taking, and exposes teaching behaviors as skills. It is also a public portfolio project aimed at entry-level AI roles.

Architecture: tools are plain typed Python functions behind an MCP server (FastMCP). Front end A is Claude Code, run by `velocity tutor <subject>` in a tutor workspace regenerated from a template; front end B (M6) is an own agent loop on the `anthropic` SDK.

Read `DESIGN.md` before making architectural decisions. It is the source of truth for goals, architecture, file locations, milestones, and open questions. If a task conflicts with it, flag the conflict instead of silently deviating, and update `DESIGN.md` when a decision changes.

## Current Status

M1 (minimum source ingestion) in progress; M2 (tutor in Claude Code) is next. See `DESIGN.md` section 4.

**Where we left off (2026-11-04):**
- Design interview done; `DESIGN.md` v0.2 records the result (subject tutor, MCP core, Claude Code front end, Obsidian logs).
- Done: `get_outline()` in `src/tutor_harness/outline.py`, with tests (stable section IDs like `2.4`, `ch2`, `2.review`, `app-f`).
- OCR works: `marker-pdf` in the `ocr` group with CPU `torch` (run with `uv run --group ocr ...`; plain `uv sync` removes the group). It needs `LLAMA_CPP_BINARY=$HOME/.local/opt/llama.cpp/b12118/llama-b12118/llama-server` (llama.cpp Vulkan build, runs on the AMD GPU) and `--force_ocr`.
- Test book: `sources/mathematics/James Stewart - Calculus - Early transcendentals (8th Edition).pdf`. Section 2.4 = PDF pp. 136-146.
- Next (rest of M1): a marker wrapper and a lazy, cached `read_section(id)` that cuts at the section heading. Drop-check repair is deferred to M7.
- Then M2: rename the package to `velocity`, add CI, an MIT license, and make the repo public, then config, the MCP server, the workspace template and launcher, hooks, and notes protection.

## Stack and Commands

Python 3.12 (pinned in `.python-version`), managed with `uv`. PyMuPDF for PDFs; `marker-pdf` (CPU `torch`, optional `ocr` group) for math OCR. Decided but not yet added: `mcp` (FastMCP) in M2, `anthropic` in M6. Ask before introducing any other dependency.

- Install: `uv sync` (add `--group ocr` for OCR)
- Run: no entry point yet (`velocity` CLI planned in M2)
- Test: `uv run pytest`
- Lint/format: `uv run ruff check .` and `uv run ruff format .`

## Repository Layout (planned)

```
src/        package code (tools, MCP server, launcher); package rename to velocity in M2
tests/      tests
template/   tutor workspace template: tutor CLAUDE.md, skills/, settings, hooks (M2)
docs/       agent docs, ADRs
sources/    local test books (gitignored)
DESIGN.md
CLAUDE.md
```

Runtime data lives outside the repo: config in `~/.config/velocity/`; subject config, session logs, and state in `<vault>/Tutor/<subject>/`; generated workspaces, the OCR cache, and session records in `~/.local/share/velocity/`.

## Conventions

- **Learner notes are read-only to the tutor.** Tool and agent code never writes outside `<vault>/Tutor/`. This must be enforced (Claude Code permission deny rules, a hook, and a test), not only stated in prompts.
- **Session logs are append-only.** Hooks capture the dialogue verbatim; the tutor only appends and never rewrites. The append-only session record outside the vault is the source of truth; learner edits in the markdown count as annotations.
- Sources are optional. When present, tutor responses cite them by stable section ID; without them, the tutor says once per session that it teaches from general knowledge.
- Tools are plain typed functions, unit-testable without a server; the MCP layer stays thin.
- Skills follow the `SKILL.md` format: YAML header with `name` and `description`, then instructions. Only names and descriptions load up front; full text loads on demand.
- Keep the tutor prompt and each skill in plain text files in the template, not buried in code.
- Generated workspace files are never hand-edited; customization goes through `subject.toml`.

## Working Agreements

- Prefer small, reviewable changes and verify them by running the code or tests.
- When extraction or parsing quality is in question (especially equations), test on a real textbook sample and show the output rather than assuming it works.
- Do not commit textbooks, copyrighted source material, or personal notes and logs. Keep them gitignored.
- Do not commit API keys; read them from environment variables.
- Ask before making a decision that appears under "Open Questions" in `DESIGN.md`, then record the answer there.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues on `sova-nova/velocity-framework` (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

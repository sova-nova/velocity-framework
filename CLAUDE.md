# CLAUDE.md

## Project

An LLM harness that makes an LLM act as a tutor or professor, grounded in a user-supplied textbook or resource. It maintains a session log and structured learner state, supports learner-owned note-taking, and exposes teaching behaviors as skills.

Read `DESIGN.md` before making architectural decisions. It is the source of truth for goals, architecture, milestones, and open questions. If a task conflicts with it, flag the conflict instead of silently deviating, and update `DESIGN.md` when a decision changes.

## Current Status

M1 (source ingestion) in progress. Work proceeds by milestone (see `DESIGN.md` section 4). Extraction findings so far are in `DESIGN.md` under "M1 findings".

**Where we left off (2026-11-02):**
- Project skeleton exists (`pyproject.toml`, `src/tutor_harness/`, `tests/`, `.gitignore`). Nothing is committed yet.
- Test book is at `sources/mathematics/James Stewart - Calculus - Early transcendentals (8th Edition).pdf`.
- Outline extraction via `pymupdf` `get_toc()` works. Equation extraction via plain text does not (broken font encoding).
- Next step: install `marker-pdf` (optional `ocr` dependency group, CPU `torch`) and test it on Section 2.4. This needs Python 3.12; the user must run `uv python install 3.12` because their `uv` blocks automatic Python downloads. `pyproject.toml` and `.python-version` were already edited for 3.12, and `.venv` was deleted, so run `uv sync` and then `uv add --group ocr marker-pdf` once 3.12 is installed.
- After that: show the OCR output next to the page image for the user to judge, then implement `get_outline()` and `read_section(id)`.

## Stack and Commands

Python 3.12 (pinned in `.python-version`), managed with `uv`. Extraction uses PyMuPDF, with `marker-pdf` (CPU `torch`, optional `ocr` group) being evaluated for math. LLM SDK is still undecided; ask before introducing a new dependency.

- Install: `uv sync`
- Run: `uv run python -m tutor_harness` (no entry point yet)
- Test: `uv run pytest`
- Lint/format: `uv run ruff check .` and `uv run ruff format .`

## Repository Layout (planned)

```
sources/    ingested textbooks (outline + section text, keyed by stable IDs)
sessions/   narrative session logs, one markdown file per session
state/      structured learner state
notes/      learner-owned notes
skills/     one folder per skill, each with a SKILL.md
src/        harness code
tests/      tests
DESIGN.md
CLAUDE.md
```

## Conventions

- **Learner notes are read-only to the agent.** Never write to or modify `notes/` from agent or tool code. The agent may read notes for critique only.
- **The agent owns the session log and state.** Logs are append-only during a session.
- Source material is referenced by stable section IDs; tutor responses should cite them.
- Tools start as plain functions in a single process. Keep their interfaces simple and well-typed so they can later be wrapped as MCP servers without rewrites.
- Skills follow the `SKILL.md` format: YAML header with `name` and `description`, then instructions. Only names and descriptions go into the system prompt up front; full text loads on demand.
- Keep the tutor system prompt and each skill in plain text files, not buried in code, so they are easy to edit and compare.

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

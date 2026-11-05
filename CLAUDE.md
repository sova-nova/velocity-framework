# CLAUDE.md

## Project

`velocity`: a local, open-source subject tutor built on an LLM. A learner picks a subject; the tutor teaches, questions, and quizzes, optionally grounded in learner-supplied sources (e.g. a textbook PDF). It keeps per-subject session logs (rendered in Obsidian) and learner state, supports learner-owned note-taking, and exposes teaching behaviors as skills. It is also a public portfolio project aimed at entry-level AI roles.

Architecture: tools are plain typed Python functions behind an MCP server (FastMCP). Front end A is Claude Code, run by `velocity tutor <subject>` in a tutor workspace regenerated from a template; front end B (M6) is an own agent loop on the `anthropic` SDK.

Read `DESIGN.md` before making architectural decisions. It is the source of truth for goals, architecture, file locations, milestones, and open questions. If a task conflicts with it, flag the conflict instead of silently deviating, and update `DESIGN.md` when a decision changes.

## Current Status

M1–M4 done; M5 (notes) is next. See `DESIGN.md` section 4.

**Where we left off (2026-11-04):**
- `velocity new "<name>"` creates a subject. `velocity tutor <subject> [--resume]` regenerates the workspace in `~/.local/share/velocity/workspaces/<subject>/` and launches Claude Code there. Hooks (`velocity hook ...`) log the dialogue, and `velocity mcp --subject <id>` serves the tools.
- Modules: `config`, `outline`, `ocr`, `pdf_source`, `session` (record + markdown log), `state` (learner state), `tools` (TutorTools), `mcp_server`, `workspace` (template, settings, notes protection), `hooks`, `cli`. Tutor prompt template: `src/velocity/template/CLAUDE.md`.
- OCR packages install by default (`[tool.uv] default-groups`); the llama.cpp server path comes from `llama_cpp_binary` in `config.toml` (or the `LLAMA_CPP_BINARY` env var). The slow real-OCR test runs only with `VELOCITY_OCR_TESTS=1`.
- Test book: `sources/mathematics/James Stewart - Calculus - Early transcendentals (8th Edition).pdf`.
- M3: `state.py` (topics, question log, threads, review scheduling); state tools in `tools.py`; summary injected at session start; `progress.md` generated in the vault. The launcher isolates the tutor with `--strict-mcp-config` and pre-approves `mcp__velocity` (see `workspace.claude_args`).
- M4 in progress: skills live in `src/velocity/template/skills/<name>/SKILL.md` and are copied into the workspace. Done: `/quiz`, `/end-session` (tool `end_session`), `/socratic`, `/explain-differently`, `/assign-problems` (tool `get_exercises`). M4 is complete; next is M5 (notes: `read_notes`, `/review-notes`).
- To test with real Claude Code without touching the user's vault: use a throwaway `XDG_CONFIG_HOME`/`XDG_DATA_HOME` with a scratch vault, build the workspace, and run `claude -p "<prompt>" --model sonnet` plus `workspace.claude_args(...)` from inside it (put the prompt before `--mcp-config`, which takes several values). Haiku does not reliably call the state tools.

## Stack and Commands

Python 3.12 (pinned in `.python-version`), managed with `uv`. PyMuPDF for PDFs; `marker-pdf` (CPU `torch`, optional `ocr` group) for math OCR. `mcp` 2.x (`MCPServer`, formerly FastMCP) for the tool server; `anthropic` is decided for M6 but not yet added. Ask before introducing any other dependency.

- Install: `uv sync` (the `ocr` group is a default group locally; CI uses `--no-group ocr`)
- Run: `uv run velocity new "<name>"`, `uv run velocity source add <subject> <file.pdf> --name <Name>`, `uv run velocity tutor <subject>` (needs `~/.config/velocity/config.toml` with `vault_path`, and `llama_cpp_binary` for OCR without an NVIDIA GPU)
- Test: `uv run pytest` (real OCR: `VELOCITY_OCR_TESTS=1 uv run --group ocr pytest`)
- Lint/format: `uv run ruff check .` and `uv run ruff format .`

## Repository Layout (planned)

```
src/        package code (tools, MCP server, launcher); src/velocity/template/ holds the tutor CLAUDE.md and skills
tests/      tests
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

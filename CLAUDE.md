# CLAUDE.md

## Project

`velocity`: a local, open-source subject tutor built on an LLM. A learner picks a subject; the tutor teaches, questions, and quizzes, optionally grounded in learner-supplied sources (e.g. a textbook PDF). It keeps per-subject session logs (rendered in Obsidian) and learner state, supports learner-owned note-taking, and exposes teaching behaviors as skills. It is also a public portfolio project aimed at entry-level AI roles.

Architecture: tools are plain typed Python functions behind an MCP server (FastMCP). Front end A is Claude Code, run by `velocity tutor <subject>` in a tutor workspace regenerated from a template; front end B (M6) is an own agent loop on the `anthropic` SDK.

Read `DESIGN.md` before making architectural decisions. It is the source of truth for goals, architecture, file locations, milestones, and open questions. If a task conflicts with it, flag the conflict instead of silently deviating, and update `DESIGN.md` when a decision changes.

## Current Status

M1–M6 built. M6 is **not merged yet**: it waits for a live API test. M7 (polish and portfolio) comes after. See `DESIGN.md` section 4.

**Where we left off (2026-11-04, paused by the user):**

*Immediate next step: finish M6.*
- PR #13 (branch `m6-own-loop`, CI green) adds `velocity chat <subject>`, the own agent loop in `agent.py`. It is open and unmerged.
- It needs a live run with the user's Anthropic API key. Don't ask for the key in chat; the user exports `ANTHROPIC_API_KEY` in their own terminal.
- The user runs `uv run velocity chat calculus` on branch `m6-own-loop`. Check: a few messages, `/quiz` with an answer, `/end-session`, the session log in Obsidian, and the cost summary printed at exit.
- Fix anything found on the same branch/PR, then the user merges #13.

*Then M7 (polish and portfolio; ask the user about scope and order first):*
- README with a demo: GIF or sample log, setup, both front ends, recommended models (Haiku does not reliably call the state tools).
- Teaching-quality evals that replay the JSONL session records.
- MIT license (the user wanted it only once the project is finished).
- Drop-check repair for OCR (prototype in `DESIGN.md` M1 findings) and non-PDF sources.
- Optional: resuming `velocity chat`, compaction for long chats, a `search` source tool.

*How the user works (see also memory):*
- One branch and PR per change, based on `main`. The user reviews and merges on GitHub with a merge commit and deletes the branch.
- Don't stack PRs; that went wrong once (#2–#5, fixed by #6).
- Merged so far: #1–#12.

*Environment facts:*
- The user's `~/.config/velocity/config.toml` points `vault_path` at the test vault `/home/sokosova/personal/test/obsidian`. Their real vault is `/home/sokosova/Documents/Linux Vault`; they will switch when ready.
- OCR cache for Stewart §2.4 and Chapter 2's Review is in `~/.local/share/velocity/sources/`. llama.cpp (Vulkan build, AMD RX 7700 XT) is at `~/.local/opt/llama.cpp/b12118/llama-b12118/llama-server`; set `llama_cpp_binary` in `config.toml` or `LLAMA_CPP_BINARY`.
- Claude Code 2.1.318 (`blockWritesOutsideWorkingDirectories` needs 2.1.314+). `gh` is installed and logged in.

*Code map:*
- Commands: `velocity new "<name>"`, `velocity subjects`, `velocity source add/list`, `velocity notes add/list`, `velocity tutor <subject> [--resume]` (front end A, Claude Code), `velocity chat <subject>` (front end B, API; on the unmerged branch). Internal: `velocity mcp --subject`, `velocity hook <event>`.
- Modules: `config`, `outline`, `ocr`, `pdf_source`, `session` (record + markdown log), `state` (learner state), `notes`, `skills`, `tools` (TutorTools), `mcp_server`, `workspace` (template, settings, notes protection, `claude_args`), `hooks`, `agent` (front end B), `cli`.
- The tutor prompt is `src/velocity/template/CLAUDE.md`; skills are in `src/velocity/template/skills/<name>/SKILL.md`. The same files back both front ends.
- Tests: about 134, `uv run pytest`. The slow real-OCR test runs only with `VELOCITY_OCR_TESTS=1`.

*How to test with real Claude Code without touching the user's vault:*
- Use a throwaway `XDG_CONFIG_HOME`/`XDG_DATA_HOME` with a scratch vault, then `velocity new`. For OCR'd sections, copy `~/.local/share/velocity/sources/*` into the throwaway data dir.
- Build the workspace, then from inside it run `claude -p "<prompt>" --model sonnet` plus `workspace.claude_args(...)`. Put the prompt before `--mcp-config`, which takes several values.
- Use `--output-format stream-json --verbose` to see tool calls. Earlier scratch vaults lived in the session scratchpad and are gone.

## Stack and Commands

Python 3.12 (pinned in `.python-version`), managed with `uv`. PyMuPDF for PDFs; `marker-pdf` (CPU `torch`, optional `ocr` group) for math OCR. `mcp` 2.x (`MCPServer`, formerly FastMCP) for the tool server; `anthropic` 0.x (pinned `<1` by `marker-pdf`) for `velocity chat`. Ask before introducing any other dependency.

- Install: `uv sync` (the `ocr` group is a default group locally; CI uses `--no-group ocr`)
- Run: `uv run velocity chat <subject>` (own loop, needs `ANTHROPIC_API_KEY`), `uv run velocity new "<name>"`, `uv run velocity source add <subject> <file.pdf> --name <Name>`, `uv run velocity tutor <subject>` (needs `~/.config/velocity/config.toml` with `vault_path`, and `llama_cpp_binary` for OCR without an NVIDIA GPU)
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

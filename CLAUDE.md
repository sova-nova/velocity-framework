# CLAUDE.md

## Project

`velocity`: a local, open-source subject tutor built on an LLM. A learner picks a subject; the tutor teaches, questions, and quizzes, optionally grounded in learner-supplied sources (e.g. a textbook PDF). It keeps per-subject session logs (rendered in Obsidian) and learner state, supports learner-owned note-taking, and exposes teaching behaviors as skills. It is also a public portfolio project aimed at entry-level AI roles.

Architecture: tools are plain typed Python functions behind an MCP server (FastMCP). Front end A is Claude Code, run by `velocity tutor <subject>` in a tutor workspace regenerated from a template; front end B (M6) is an own agent loop on the `anthropic` SDK.

Read `DESIGN.md` before making architectural decisions. It is the source of truth for goals, architecture, file locations, milestones, and open questions. If a task conflicts with it, flag the conflict instead of silently deviating, and update `DESIGN.md` when a decision changes.

## Current Status

M1–M6 done and merged. M7 (polish and portfolio) is done except the postponed eval full run. M8 (web front end) is in progress. See `DESIGN.md` section 4.

**Where we left off (2026-11-06):** PR B is open as #26 (branch `web-chat-claude-code`). The user tested it live and it worked. Next session: once #26 is merged, start PR C (web chat on the own loop) from a fresh branch off `main`.

**Earlier context:**

*M7 plan (agreed 2026-11-05; see `DESIGN.md` sections 4 and 5), one PR each, in order:*
1. Fixes from the M6 live test: done (#15).
2. README with a demo (a real session excerpt, used with the user's permission), setup, both front ends, model advice.
3. Teaching-quality evals, in 3 PRs (the user chose: commit cases from their sessions, compare Opus and Sonnet, they will hand-label ~15 cases, budget ~$5 per full run):
   - PR 1 (#17, merged): cases, next-turn replay harness, code checks. Dry run: $0.25 for 3 cases on two models.
   - PR 2 (#18, branch `evals-judge`): LLM judge (Opus 5.5, pass/fail rubric) and the labeling tool (`python -m evals.label export/agree`). Judge dry run: $0.40 for 3 cases.
   - PR 3, **postponed by the user (2026-11-06) to limit spending**: full run (estimated $5–6; the user hadn't yet chosen between allowing ~$6.50 once, a Sonnet judge, or accepting gaps), the user labels ~15 replies from it, judge agreement measured, results in the README. Ask before starting it.
4. Source improvements: drop-check repair for OCR (done, #21 and #22), then non-PDF sources (branch `non-pdf-sources`: Markdown, HTML, EPUB; see `DESIGN.md` "M7 notes: non-PDF sources").
- Optional later: resuming `velocity chat`, compaction for long chats, a `search` source tool.
- M7 is done except the postponed eval full run.

*M8, web front end (planned 2026-11-06; decisions in `DESIGN.md` section 5):* both engines selectable, chat + session history, Starlette + plain JS, for daily use and as a demo. PRs, reordered after a design review (grilling) on 2026-11-06:
1. Server + history: done (#24).
2. PR A, session titles + resume/continue (core, all front ends; branch `session-titles-continue`).
3. PR B (#26, open, branch `web-chat-claude-code`; PR A merged as #25), web chat on Claude Code headless. Tested live: skills, titles, the "?" popup, reload, pause/resume, end/continue, and the switch dialog (Resume/Continue in History while another session is open offers to pause it first). History reloads each time its tab opens. The experiment confirmed: one JSON line per message (`{"type":"user","message":{"role":"user","content":...}}`); the UserPromptSubmit/Stop hooks log stdin messages; `/quiz` works; closing stdin exits 0 and runs SessionEnd; `--resume <claude id>` reattaches to the same velocity session. Decisions: one process per chat; one open session per subject; End session (prominent) + Pause buttons with a "?" help popup; 30-minute idle timeout = pause; reopening the page continues a running chat; model picked at start (default Sonnet, last choice remembered); tool calls as grey lines; no dollar amounts; `--permission-prompts none`; resuming a `velocity chat` session attaches a new Claude Code conversation that reads the log first.
4. PR C, web chat on the own loop (`agent.py` engine behind the same `ChatManager`), selectable next to Claude Code, with the cost shown (the "no dollar amounts" decision applies only to the Claude Code engine). The user exports `ANTHROPIC_API_KEY` in their own terminal and runs any paid tests themselves.
5. PR D, polish + README demo (incl. a stop button for long replies).
Ask about the MIT license after M8.

*How the user works (see also memory):*
- One branch and PR per change, based on `main`. The user reviews and merges on GitHub with a merge commit and deletes the branch.
- Don't stack PRs; that went wrong once (#2–#5, fixed by #6).
- Merged so far: #1–#25.
- Edit one file at a time with Edit or Write; the user rejected a large multi-file edit script as too big to review.
- To try the web UI: `uv run velocity web --no-browser --port 8799` (127.0.0.1 only). Page files are read from disk, so changes to the page only need a browser reload.

*Environment facts:*
- The user's `~/.config/velocity/config.toml` points `vault_path` at the test vault `/home/sokosova/personal/test/obsidian`. Their real vault is `/home/sokosova/Documents/Linux Vault`; they will switch when ready.
- OCR cache for Stewart §2.4 and Chapter 2's Review is in `~/.local/share/velocity/sources/`. llama.cpp (Vulkan build, AMD RX 7700 XT) is at `~/.local/opt/llama.cpp/b12118/llama-b12118/llama-server`; set `llama_cpp_binary` in `config.toml` or `LLAMA_CPP_BINARY`.
- Claude Code 2.1.318 (`blockWritesOutsideWorkingDirectories` needs 2.1.314+). `gh` is installed and logged in.

*Code map:*
- Commands: `velocity new "<name>"`, `velocity subjects`, `velocity source add/list/recheck`, `velocity notes add/list`, `velocity tutor <subject> [--resume]` (front end A, Claude Code), `velocity chat <subject> [--model opus|sonnet|haiku]` (front end B, API; `/model` switches mid-chat), `velocity web [subject]` (front end C, local web UI, M8 in progress). Internal: `velocity mcp --subject`, `velocity hook <event>` (events: session-start, user-prompt, stop, session-end, guard, tool-use).
- Modules: `config`, `outline`, `ocr`, `dropcheck`, `pdf_source`, `text_source` (Markdown, HTML, EPUB), `web` (Starlette app; page in `web_static/`), `chat` (web chats), `claude_engine` (headless Claude Code), `session` (record + markdown log), `state` (learner state), `notes`, `skills`, `tools` (TutorTools), `mcp_server`, `workspace` (template, settings, notes protection, `claude_args`), `hooks`, `agent` (front end B), `cli`.
- The tutor prompt is `src/velocity/template/CLAUDE.md`; skills are in `src/velocity/template/skills/<name>/SKILL.md`. The same files back both front ends.
- Evals: `evals/` (cases, replay, checks, judge, label, run); `uv run python -m evals.run --list`. See `DESIGN.md` "M7 notes: evals".
- Tests: about 229, `uv run pytest` (web chat uses `tests/fake_claude.py` instead of Claude Code). The slow real-OCR test runs only with `VELOCITY_OCR_TESTS=1`.

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
- **Session logs are append-only.** Hooks capture the dialogue verbatim; the tutor only appends and never rewrites. The append-only session record outside the vault is the source of truth; learner edits in the markdown count as annotations. One exception (decided 2026-11-06): velocity updates the `title:` line of a log's properties block when the tutor sets the session's title. The dialogue itself is never rewritten.
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

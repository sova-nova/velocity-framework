# CLAUDE.md

## Project

`velocity`: a local, open-source subject tutor built on an LLM. A learner picks a subject; the tutor teaches, questions, and quizzes, optionally grounded in learner-supplied sources (e.g. a textbook PDF). It keeps per-subject session logs (rendered in Obsidian) and learner state, supports learner-owned note-taking, and exposes teaching behaviors as skills. It is also a public portfolio project aimed at entry-level AI roles.

Architecture: tools are plain typed Python functions behind an MCP server (FastMCP). Front end A is Claude Code, run by `velocity tutor <subject>` in a tutor workspace regenerated from a template; front end B (M6) is an own agent loop on the `anthropic` SDK.

Read `DESIGN.md` before making architectural decisions. It is the source of truth for goals, architecture, file locations, milestones, and open questions. If a task conflicts with it, flag the conflict instead of silently deviating, and update `DESIGN.md` when a decision changes.

## Current Status

M1–M6 done and merged. M7 (polish and portfolio) is done except the postponed eval full run. M8 (web front end) is done except the postponed README demo. M9 (fleshing out) is done. M10 (everything from the web page) is done, compaction included (2026-11-10). See `DESIGN.md` section 4.

**Where we left off (2026-11-10):** Nothing is open; everything through #45 is merged. M10, "everything from the web page" (the user wants the page to do all the terminal can), is done except compaction. Its last PRs: the clarity pass (#41 header, Chat and colors; #42 History and Library; #43 dialogs; decisions and D1–D5 as built in the `DESIGN.md` decisions log, 2026-11-09: "Book" as the page's word, the Chalkboard palette, a color per subject, save as you go), a fix for the engine switch losing its highlight (#44, a bug from #41 the user found), and two ideas of the user's in #45: "Add a book…" in the Library tab adds a book to the library only (duplicates refused by content), and "Prepare…" prepares a whole PDF from there, with progress and Cancel (`velocity source prepare --file <pdf>`; see `DESIGN.md` "Notes: the sources library"). **Next:** ask the user what they want. The remaining planned item is compaction for long own-loop chats (low priority; check the API's built-in compaction first, see the `claude-api` skill). The user likes suggesting features as they test, so expect new ideas; each gets its own branch and PR from `main`. Earlier M10 PRs, all merged: subjects on the page (#34), the sources library (#35), test isolation (#36: `tests/conftest.py`), the Library tab (#37), first-run setup and settings (#38), Obsidian strongly recommended (#39), recheck on the page (#40). M9 is done (#29–#33). To check the page yourself: headless Firefox over WebDriver BiDi (see `DESIGN.md` "Notes: source reader"), on a throwaway `XDG_CONFIG_HOME`/`XDG_DATA_HOME`. Use its own profile folder, and stop only that Firefox, by its process id: never `pkill firefox` (the user's own browser), and not `pkill -f` with a pattern that also matches your own shell command. Postponed by the user: the README demo (two screenshots as `docs/images/web-chat.png` and `docs/images/web-history.png` from the test vault, plus a short video uploaded to GitHub as a `user-attachments` link), the license (until the app is more fleshed out; don't propose it again until then), a tester guide (when they invite testers), and the eval full run.

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
3. PR B (merged as #26; PR A merged as #25), web chat on Claude Code headless. Tested live: skills, titles, the "?" popup, reload, pause/resume, end/continue, and the switch dialog (Resume/Continue in History while another session is open offers to pause it first). History reloads each time its tab opens. The experiment confirmed: one JSON line per message (`{"type":"user","message":{"role":"user","content":...}}`); the UserPromptSubmit/Stop hooks log stdin messages; `/quiz` works; closing stdin exits 0 and runs SessionEnd; `--resume <claude id>` reattaches to the same velocity session. Decisions: one process per chat; one open session per subject; End session (prominent) + Pause buttons with a "?" help popup; 30-minute idle timeout = pause; reopening the page continues a running chat; model picked at start (default Sonnet, last choice remembered); tool calls as grey lines; no dollar amounts; `--permission-prompts none`; resuming a `velocity chat` session attaches a new Claude Code conversation that reads the log first.
4. PR C (branch `web-chat-own-loop`), web chat on the own loop (`api_engine.py` behind the same `ChatManager`), selectable next to Claude Code, with the cost shown (the "no dollar amounts" decision applies only to the Claude Code engine). Decided 2026-11-09 (see `DESIGN.md` decisions log): Claude Code is the default engine, and the last engine and model are remembered; the own loop resumes any paused session by rebuilding its history from the record; Resume preselects the engine and model that ran the session last; a session total plus a small per-reply cost. The user exports `ANTHROPIC_API_KEY` in their own terminal and runs any paid tests themselves.
5. PR D (merged as #28): Stop, Retry, a thinking indicator, the third-party reminder on the page for web chats. The README demo moved to its own later PR.
The license is postponed until the application is more fleshed out (user, 2026-11-09).

*How the user works (see also memory):*
- One branch and PR per change, based on `main`. The user reviews and merges on GitHub with a merge commit and deletes the branch.
- Don't stack PRs; that went wrong once (#2–#5, fixed by #6).
- Merged so far: #1–#45. The user deletes merged branches themselves.
- Edit one file at a time with Edit or Write; the user rejected a large multi-file edit script as too big to review.
- To try the web UI: `uv run velocity web --no-browser --port 8799` (127.0.0.1 only). Page files are read from disk and sent with `no-cache`, so changes to the page only need a browser reload. The user often has their own server on 8799; for your own checks use another port (8811 worked) with a throwaway `XDG_CONFIG_HOME`/`XDG_DATA_HOME` (add `llama_cpp_binary` from the user's config for real OCR), and stop only that server, by the process id listening on its port.

*Environment facts:*
- The user's `~/.config/velocity/config.toml` points `vault_path` at the test vault `/home/sokosova/personal/test/obsidian`. Their real vault is `/home/sokosova/Documents/Linux Vault`; they will switch when ready.
- OCR cache for Stewart §2.4 and Chapter 2's Review is in `~/.local/share/velocity/sources/`. llama.cpp (Vulkan build, AMD RX 7700 XT) is at `~/.local/opt/llama.cpp/b12118/llama-b12118/llama-server`; set `llama_cpp_binary` in `config.toml` or `LLAMA_CPP_BINARY`.
- Claude Code 2.1.318 (`blockWritesOutsideWorkingDirectories` needs 2.1.314+). `gh` is installed and logged in.

*Code map:*
- Commands: `velocity new "<name>"`, `velocity subjects`, `velocity source add/list/remove/rename/recheck/prepare` (`prepare --file <pdf>` for a book in no subject), `velocity notes add/list`, `velocity tutor <subject> [--resume]` (front end A, Claude Code), `velocity chat <subject> [--model opus|sonnet|haiku]` (front end B, API; `/model` switches mid-chat), `velocity web [subject]` (front end C, local web UI). Internal: `velocity mcp --subject`, `velocity hook <event>` (events: session-start, user-prompt, stop, session-end, guard, tool-use).
- Modules: `config`, `outline`, `ocr`, `dropcheck`, `pdf_source`, `text_source` (Markdown, HTML, EPUB), `web` (Starlette app; page in `web_static/`: `app.js`, `reader.js` for the source reader, `subjects.js` for managing subjects, `library.js` for the Library tab, `setup.js` for setup and settings), `chat` (web chats), `engine` (engine interface, `ChatEvent`), `claude_engine` (headless Claude Code), `api_engine` (the own loop for web chat), `search` (keyword search over sources), `prepare` (OCR ahead of time: the job and its progress file), `uploads` (sources uploaded from the web page), `library` (the sources library), `setup` (first-run setup, settings, the app that swaps itself), `history` (rebuild API messages from a record; shared with the evals), `session` (record + markdown log), `state` (learner state), `notes`, `skills`, `tools` (TutorTools), `mcp_server`, `workspace` (template, settings, notes protection, `claude_args`), `hooks`, `agent` (front end B), `cli`.
- The tutor prompt is `src/velocity/template/CLAUDE.md`; skills are in `src/velocity/template/skills/<name>/SKILL.md`. The same files back both front ends.
- Evals: `evals/` (cases, replay, checks, judge, label, run); `uv run python -m evals.run --list`. See `DESIGN.md` "M7 notes: evals".
- Tests: about 313, `uv run pytest` (web chat uses `tests/fake_claude.py` instead of Claude Code, and a scripted API with in-process tools for the own loop). The slow real-OCR test runs only with `VELOCITY_OCR_TESTS=1`.

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
- **Session logs are append-only.** Hooks capture the dialogue verbatim; the tutor only appends and never rewrites. The append-only session record outside the vault is the source of truth; learner edits in the markdown count as annotations. One exception (decided 2026-11-06): velocity updates the `title:` line of a log's properties block when the tutor sets the session's title. The dialogue itself is never rewritten. A second (decided 2026-11-10): an **empty** session (nothing but its start and end) is removed when it ends, record and log, unless the learner wrote in its log.
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

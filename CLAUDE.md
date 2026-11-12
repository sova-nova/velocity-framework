# CLAUDE.md

## Project

`velocity`: a local, open-source subject tutor built on an LLM. A learner picks a subject; the tutor teaches, questions, and quizzes, optionally grounded in learner-supplied sources (e.g. a textbook PDF). It keeps per-subject session logs (rendered in Obsidian) and learner state, supports learner-owned note-taking, and exposes teaching behaviors as skills. It is also a public portfolio project aimed at entry-level AI roles.

Architecture: tools are plain typed Python functions behind an MCP server (FastMCP). Front end A is Claude Code, run by `velocity tutor <subject>` in a tutor workspace regenerated from a template; front end B (M6) is an own agent loop on the `anthropic` SDK.

Read `DESIGN.md` before making architectural decisions. It is the source of truth for goals, architecture, file locations, milestones, and open questions. If a task conflicts with it, flag the conflict instead of silently deviating, and update `DESIGN.md` when a decision changes.

## Current Status

M1–M6 done and merged. The security review before the first release is done (2026-11-12). M7 (polish and portfolio) is done except the postponed eval full run. M8 (web front end) is done except the postponed README demo. M9 (fleshing out) is done. M10 (everything from the web page) is done, compaction included (2026-11-10). See `DESIGN.md` section 4.

**Where we left off (2026-11-12, evening):** Everything through #85 is merged. **The security review is done** (`DESIGN.md` section 6 has the findings; the decisions log has each fix). Fixed: the page's libraries bundled in `web_static/vendor/` (#76, rebuilt by `packaging/vendor.py`; the `.gitignore` rule is `/build/` so PDF.js's `legacy/build/` is committed); the free key's variable must end in `_API_KEY`, never `ANTHROPIC_API_KEY` (#77); a Content-Security-Policy on every response (#78); release checksums (`SHA256SUMS`), write permission only in the publish job, actions pinned by commit SHA, `appimagetool` by sha256, Inno Setup 6.7.1 (#80); math put in after sanitizing (#81). Decided by the user: on Claude Code the tutor may use WebSearch and read Wikipedia (`ALLOWED_TOOLS`, `template/web.md`; other sites ask first or are blocked; #82); a README note for shared computers instead of a token (#83); the tutor gets only the built-in tools `Skill, ToolSearch, WebSearch, WebFetch` (`TUTOR_TOOLS`, `--tools`; #84). CI runs macOS only after a merge to `main` (#79; its runners kept PRs waiting). Repeatable checks: `tests/test_security.py` (every route; in CI) and `tests/browser_check.py` (headless Firefox, by hand: `uv run --with websockets python tests/browser_check.py`). **Next, in order:** (1) the user (or a tester) tries the Windows build: open a book in the reader and look at chat math (the libraries are now bundled, served with explicit MIME types for Windows); (2) the user tags `v0.1.0` (`git tag v0.1.0 && git push origin v0.1.0`), the first real run of the `SHA256SUMS` step; (3) macOS as its own PR (ask first). Still the user's call: the README demo, the eval full run, judging the free-model runs with Sonnet, and a per-launch token for the local server (if testers share computers). **Lessons:** before pushing new files, check `git status --ignored` for all of them, not one (#76 lost two files to an ignore rule); and never pass `claude_args` through an unquoted `$(shlex.join ...)` in a shell (the rules get literal quotes); run Claude Code from Python with an argument list, as velocity does.

**Before that (2026-11-12):** Everything through #74 was merged. Since the paragraph below: Windows (#68: installer and portable zip, unsigned; CI on Linux, Windows and macOS). An AppImage crash on the user's Fedora (#69: the spec now leaves out the system's graphics, C++ and desktop libraries, `LINUX_SYSTEM_LIBRARIES`). Books didn't open in the app (#70: PDF.js's `legacy/` build, since Qt WebEngine is Chromium 140). Settings in tabs, General, Claude, Free model and This computer, plus Colors (system, light or dark; #71: `theme` in `config.toml`, put on `<html>` by the server). "Saved." in the buttons' row so the dialog keeps its size (#72). The app menu icon named by its full path, since Plasma's icon cache missed it by name (#73). The app uses the same port each launch, 8766–8775 (`desktop.APP_PORTS`), so the page's storage survives restarts (#74). The user tried the Linux build and it works. (Its next steps, the Windows check and the security review, are in the paragraph above.) **Lesson (#69):** try a packaged build on a real display, built where the release is built (the CI artifact), not only offscreen or locally.

**Before that (2026-11-11, later):** #59 (fewer questions, `/next`) and #60 ("Partly right", the `partial` outcome) are merged. The free engine was grilled (decisions log, "the free engine"); PR 1 (#61: `openai_engine.py`, Settings section with presets and Test) and PR 2 (#62: evals on free models with `--models free:<model>`, the README's "Free models" section; results in `DESIGN.md` "Notes: evals on free models") are merged, and so is `velocity chat --engine openai` (#63). Ollama is installed on the user's machine with `qwen3:4b` and `qwen3:8b` pulled; their GPU has 12 GB. Start Ollama with `OLLAMA_CONTEXT_LENGTH=32768`, and stop only your own `ollama serve`, by the process listening on 11434. Still the user's call: judging the free runs with Sonnet (~$1–2, they run it with their key). **The desktop app** was grilled (decisions log, "the desktop app": for people who don't use a terminal; five PRs). PR 1 (#64: `velocity app`, the window, single instance, icon, app menu entry), PR 2 (#65: keys in the system keyring, `keys.py`) and PR 3 (#66: `ollama.py`, Download model in Settings) are merged; PR 4 (#67: the AppImage, `packaging/`, `runtime.self_command`, the release workflow, Check for updates) is merged. PR 5, **Windows** (#68: an installer and a portable zip, unsigned; CI on Linux, Windows and macOS; many Windows fixes, see the decisions log), is merged; the user's testers use Windows. The user's Ollama also has `velocity-qwen3-4b` (velocity's copy, 32K context). (The next steps are in the paragraph above.) Build locally with `packaging/build_appimage.sh` (output in `build/`, gitignored); Windows builds only in CI here. Code must stay portable: name `encoding="utf-8"` on every text read and write, use `filelock` (not `fcntl`), `runtime.self_command` to start velocity, and never `os.kill(pid, 0)`. **Never touch the user's real keyring:** tests get an in-memory one (`tests/conftest.py`); for live checks set `PYTHON_KEYRING_BACKEND` to a throwaway backend (see the decisions log, "PR 2 as built"). To check the window without showing it on the user's screen: `QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-sandbox"` and a driver that wraps `webview.start` (see the decisions log). **Testing the own loop or free engine on a throwaway `XDG_*`:** the tool server now gets the whole environment; before #61 it didn't, and it wrote to the user's real vault.

**Where we left off (2026-11-11):** Everything through #57 is merged (#46–#57: History search, LaTeX in every view that shows chat text, `velocity chat --resume`, the idle pause with Resume on the page, and colored question/feedback/hint callouts with answer dividers). **Next:** two ideas of the user's, written up in `DESIGN.md` section 6, "Open Questions": a free way to run velocity (an OpenAI-compatible engine for local models or hosted free tiers) and a desktop app (a launcher, then a `pywebview` window). **Grill the user on them before building anything** (the `grilling` skill), starting with the free engine unless they say otherwise. The rest of this paragraph is from 2026-11-10: everything through #45 was merged then. M10, "everything from the web page" (the user wants the page to do all the terminal can), is done except compaction. Its last PRs: the clarity pass (#41 header, Chat and colors; #42 History and Library; #43 dialogs; decisions and D1–D5 as built in the `DESIGN.md` decisions log, 2026-11-09: "Book" as the page's word, the Chalkboard palette, a color per subject, save as you go), a fix for the engine switch losing its highlight (#44, a bug from #41 the user found), and two ideas of the user's in #45: "Add a book…" in the Library tab adds a book to the library only (duplicates refused by content), and "Prepare…" prepares a whole PDF from there, with progress and Cancel (`velocity source prepare --file <pdf>`; see `DESIGN.md` "Notes: the sources library"). **Next:** ask the user what they want. The remaining planned item is compaction for long own-loop chats (low priority; check the API's built-in compaction first, see the `claude-api` skill). The user likes suggesting features as they test, so expect new ideas; each gets its own branch and PR from `main`. Earlier M10 PRs, all merged: subjects on the page (#34), the sources library (#35), test isolation (#36: `tests/conftest.py`), the Library tab (#37), first-run setup and settings (#38), Obsidian strongly recommended (#39), recheck on the page (#40). M9 is done (#29–#33). To check the page yourself: headless Firefox over WebDriver BiDi (see `DESIGN.md` "Notes: source reader"), on a throwaway `XDG_CONFIG_HOME`/`XDG_DATA_HOME`. Use its own profile folder, and stop only that Firefox, by its process id: never `pkill firefox` (the user's own browser), and not `pkill -f` with a pattern that also matches your own shell command. Postponed by the user: the README demo (two screenshots as `docs/images/web-chat.png` and `docs/images/web-history.png` from the test vault, plus a short video uploaded to GitHub as a `user-attachments` link), the license (until the app is more fleshed out; don't propose it again until then), a tester guide (when they invite testers), and the eval full run.

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
- Commands: `velocity new "<name>"`, `velocity subjects`, `velocity source add/list/remove/rename/recheck/prepare` (`prepare --file <pdf>` for a book in no subject), `velocity notes add/list`, `velocity tutor <subject> [--resume]` (front end A, Claude Code), `velocity chat <subject> [--model opus|sonnet|haiku] [--engine api|openai]` (front end B, API; `/model` switches mid-chat; `--engine openai` for the free model), `velocity web [subject]` (front end C, local web UI), `velocity app [subject]` (the same page in its own window), `velocity install-launcher` (app menu entry). Internal: `velocity mcp --subject`, `velocity hook <event>` (events: session-start, user-prompt, stop, session-end, guard, tool-use).
- Modules: `config`, `outline`, `ocr`, `dropcheck`, `pdf_source`, `text_source` (Markdown, HTML, EPUB), `web` (Starlette app; page in `web_static/`: `app.js`, `reader.js` for the source reader, `subjects.js` for managing subjects, `library.js` for the Library tab, `setup.js` for setup and settings), `chat` (web chats), `engine` (engine interface, `ChatEvent`), `claude_engine` (headless Claude Code), `api_engine` (the own loop for web chat), `openai_engine` (the free engine: local models or free tiers over the OpenAI protocol, its own small loop), `search` (keyword search over sources), `prepare` (OCR ahead of time: the job and its progress file), `uploads` (sources uploaded from the web page), `library` (the sources library), `setup` (first-run setup, settings, the app that swaps itself), `desktop` (the desktop window, one app at a time, the app menu entry), `keys` (API keys: environment, else the system keyring), `ollama` (finding Ollama, Download model), `runtime` (packaged or not; how velocity starts itself), `filelock` (cross-platform locks), `history` (rebuild API messages from a record; shared with the evals), `session` (record + markdown log), `state` (learner state), `notes`, `skills`, `tools` (TutorTools), `mcp_server`, `workspace` (template, settings, notes protection, `claude_args`), `hooks`, `agent` (front end B), `cli`.
- The tutor prompt is `src/velocity/template/CLAUDE.md`; skills are in `src/velocity/template/skills/<name>/SKILL.md`. The same files back both front ends.
- Evals: `evals/` (cases, replay, checks, judge, label, run); `uv run python -m evals.run --list`. See `DESIGN.md` "M7 notes: evals".
- Tests: about 440, `uv run pytest` (web chat uses `tests/fake_claude.py` instead of Claude Code, and a scripted API with in-process tools for the own loop). The slow real-OCR test runs only with `VELOCITY_OCR_TESTS=1`. Security: `tests/test_security.py` checks rules over every route; `tests/browser_check.py` checks the page in headless Firefox (by hand).

*How to test with real Claude Code without touching the user's vault:*
- Use a throwaway `XDG_CONFIG_HOME`/`XDG_DATA_HOME` with a scratch vault, then `velocity new`. For OCR'd sections, copy `~/.local/share/velocity/sources/*` into the throwaway data dir.
- Build the workspace, then from inside it run `claude -p "<prompt>" --model sonnet` plus `workspace.claude_args(...)`. Put the prompt before `--mcp-config`, which takes several values.
- Use `--output-format stream-json --verbose` to see tool calls. Earlier scratch vaults lived in the session scratchpad and are gone.

## Stack and Commands

Python 3.12 (pinned in `.python-version`), managed with `uv`. PyMuPDF for PDFs; `marker-pdf` (CPU `torch`, optional `ocr` group) for math OCR. `mcp` 2.x (`MCPServer`, formerly FastMCP) for the tool server; `anthropic` 0.x (pinned `<1` by `marker-pdf`) for `velocity chat`; `openai` 2.x for the free engine (any OpenAI-compatible server). Ask before introducing any other dependency.

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
- Do not commit API keys; read them from environment variables or the system keyring (`keys.py`), never from velocity's own files, and never send them back to the page.
- Ask before making a decision that appears under "Open Questions" in `DESIGN.md`, then record the answer there.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues on `sova-nova/velocity-framework` (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

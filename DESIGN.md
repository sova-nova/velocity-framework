# Velocity: Design Document

Status: draft v0.2 (2026-11-04). Rewritten after a design interview; v0.1 framed the project as a textbook tutor.

## 1. Purpose

`velocity` is a local, open-source **subject tutor** built on an LLM. A learner picks a subject ("Calculus", "Network security") and the tutor teaches lessons, asks questions, runs quizzes, and tests their knowledge. Learners can attach **sources** (a textbook PDF, later other formats) when they want teaching grounded in specific material, but sources are optional. Every session produces a log that serves both as a study artifact for the learner and as persistent context for the tutor.

The project is also a public portfolio piece aimed at entry-level AI roles. It should demonstrate an MCP server, agent integration (Claude Code), an agent loop of its own, and measured (evaluated) LLM behavior.

### Goals

- One-on-one tutoring dialogue on any subject, optionally grounded in learner-supplied sources.
- A session log the tutor writes, which the learner reads in Obsidian (math rendered) and can annotate.
- Persistent per-subject learner state: what has been covered, what went poorly, what is due for review.
- Learner-owned note-taking, supported but never replaced by the tutor.
- A skills system for invoking teaching behaviors on demand (e.g. `/quiz`, `/socratic`).
- Anyone can install and run it locally with their own Anthropic access.

### Non-goals (for now)

- Hosting, accounts, or multi-user deployment. Each user runs it locally and brings their own key or Claude plan.
- Training or fine-tuning models.
- Automatic grading of free-form proofs or long-form work beyond LLM judgment.
- A custom GUI before M8. Obsidian renders the session log; a web front end is planned as M8.
- Writing, restructuring, or reorganizing the learner's notes (see Rubicon in section 7).

## 2. Core Concepts

### 2.1 Subjects and sources

A **subject** is the core unit. Learner state, session logs, and review schedules are tracked per subject. Each subject has a `subject.toml` holding its sources, the note folders the tutor may read, and persona customizations.

A subject has **zero or more sources**. With sources, the tutor uses them and cites them by stable section ID (e.g. "Stewart §2.4"). Without sources, the tutor teaches from general knowledge and says so **once** at the start of the session, not on every answer. When a session has sources, the tutor marks any answer that does not come from them as general knowledge.

### 2.2 The tutor

A single tutor prompt defines the persona and pedagogy. Defaults:

- Diagnose what the learner already knows before teaching.
- Ask before telling; give hints before answers.
- Interleave earlier material into new questions (retrieval practice).
- Be honest about errors, including its own. No agreeing for the sake of comfort.
- Tie explanations to sources when the subject has them, with citations.

Per-subject customization comes from `subject.toml` (extra persona instructions, default skills).

### 2.3 Sessions and the session log

One `velocity tutor <subject>` run is one session, which produces one log. Resuming the same Claude Code session continues the same log.

The log has two forms:

| Form | Location | Owner | Purpose |
|---|---|---|---|
| Session record (append-only, structured, e.g. JSONL) | `~/.local/share/velocity/` | Tool | Source of truth for history. The tutor reads past sessions from here. Kept from M2 on so sessions can later be replayed for evals. |
| Session log (markdown) | `<vault>/Tutor/<subject>/sessions/` | Tool writes, learner annotates | The readable view, rendered live in Obsidian. |

- The dialogue is captured **verbatim by hooks**, not by the model: learner messages are appended as `**You:** …` and tutor replies as `**Tutor:** …`. The tutor adds its own blocks (worked problems, recaps) with `append_log` during its turn.
- The tutor only **appends**. It never rewrites earlier content, and it treats past sessions' logs as read-only. The one exception is the `title:` property (see the 2026-11-06 decisions).
- **Titles:** the tutor names each session with `set_session_title` once its topic is clear and refines it at wrap-up. The title is recorded in the session record and shown as the log's `title:` property, in `list_sessions`, `velocity sessions` and the web History. File names stay date-based.
- **Interrupted vs. finished sessions:** a session that ended without a wrap-up (paused, crashed, closed) can be resumed in place. A wrapped-up session is final, but it can be **continued**: a new session with `continues: "[[<id>]]"` in its properties, whose start context includes the earlier recap and tells the tutor to read that session first. Each log stays one sitting.
- The learner may annotate the markdown. Text in the markdown that is not in the record is treated as a **learner annotation** and passed to the tutor as such.
- If the markdown has **lost** text compared to the record (an accidental deletion), the tutor says so and offers to restore it. It never silently overwrites the learner's edits.
- `/end-session` does a proper wrap-up: it catches up on progress, asks one retrieval question, writes a recap block (also shown to the tutor at the next session start), and closes the log after the tutor's final message. If the learner just quits, a session-end hook closes the log with an "ended without wrap-up" footer.
- Resuming a session that ended without wrap-up reopens its log with a "*Session resumed.*" marker. A wrapped-up session is final; resuming the conversation starts a new log.

### 2.4 Learner state

Structured per-subject state in `<vault>/Tutor/<subject>/state.json`, written only by velocity's tools. It has three parts:

- **Topics:** name, status (introduced / practicing / solid), source section, first and last seen, review interval, next review.
- **Question log:** date, session, topic, question, outcome (correct / hinted / incorrect), and a short note.
- **Open threads:** things to come back to, opened and closed by the tutor.

Review scheduling: a correct answer doubles a topic's interval (1, 2, 4, 8… days), an answer that needed hints keeps it, and a wrong answer resets it to one day. This is deliberately simple and predictable.

The tutor updates state **during** the session with small typed tools (`record_question` after every answered question, `set_topic_status`, `open_thread`, `close_thread`, `read_state`), so progress survives sessions that end without wrap-up. A compact summary (due reviews, weak spots, open threads, recent topics) is loaded at session start. A readable `progress.md` is regenerated beside `state.json` after every change; the learner corrects it by telling the tutor, not by editing it.

### 2.5 Learner-owned notes

Learning benefits from effortful retrieval and from restating ideas in one's own words, so the tutor must not write the learner's notes for them.

- Notes live wherever the learner keeps them, typically an Obsidian vault. Each subject lists the **note folders** the tutor may read (e.g. `Math/Calculus/`), not the whole vault. `velocity notes add <subject> <folder>` adds one.
- The tutor reads notes only through `list_notes` and `read_note`. These see only markdown files inside the subject's note folders, skip hidden folders (`.trash`), refuse paths that escape a folder (`../`, symlinks), and never return private notes.
- The tutor has **read-only** access to notes. It writes only in `<vault>/Tutor/`. This is **enforced**, not just requested in the prompt: Claude Code permission rules deny Write/Edit on the vault outside `Tutor/`, a hook blocks writes to note paths as a second layer, and a test checks the generated settings.
- Notes with `private: true` in their front matter are never read.
- Every session starts with a reminder that notes and logs the tutor reads are sent to a third-party API. In the terminal front ends the tutor gives it; in the web UI the page shows it where sessions start, and the tutor is told not to repeat it (decided 2026-11-09).
- The tutor prompts note-taking at natural moments, and **critiques** notes on request (gaps, misconceptions, missing connections).
- Optional flashcards, ideally written by the learner and reviewed by the tutor.

### 2.6 Sources and ingestion

Naive chunk-and-embed RAG is a poor fit for teaching, because textbooks have structure the tutor should use.

1. Extract the table of contents and build an outline with stable section IDs (`2.4`, `ch2`, `2.review`, `app-f`, `preface`).
2. Extract section text on demand (lazily, on first `read_section`), with math as LaTeX, and cache it.
3. Later: extract problem sets separately so the tutor can assign exercises; ingest a solution manual if available.
4. Non-PDF sources (Markdown, HTML, EPUB) behind the same interface: built in M7 (see "M7 notes: non-PDF sources").

Source tools: `get_outline()`, `read_section(id)`, and `get_exercises(section_id)`, which returns the section text from its EXERCISES heading on. `search_source(query, source?)` finds the sections and pages that cover a topic (added 2026-11-09; see "Notes: source search").

OCR (marker, torch, llama.cpp) is an **optional install extra**, so the base install stays light.

### 2.7 Skills

A skill is a folder containing a `SKILL.md`: a YAML header (`name`, `description`) followed by plain-language instructions, optionally with scripts or reference files. Only names and descriptions load up front; the full text loads when the skill is invoked or judged relevant. Claude Code supports this format natively, and the own agent loop (M6) will implement the same loading.

Planned skills:

| Skill | Behavior |
|---|---|
| `/quiz` | Short quiz on recent material, one question at a time, hints before answers, results logged |
| `/socratic` | Guide the learner to the answer without giving it |
| `/explain-differently` | Re-explain the last concept with a different angle or analogy |
| `/review-notes` | Critique the learner's notes (read-only) |
| `/assign-problems` | Pull exercises from a source, assign, and check |
| `/end-session` | Wrap up the log and state, schedule reviews, prompt for notes |

## 3. Architecture

One core, two front ends.

```
            Front end A (M2)                    Front end B (M6)
   velocity tutor <subject>                own agent loop
   -> Claude Code in a generated           (anthropic SDK, API key)
      tutor workspace                               |
            |                                       |
            +------------------+--------------------+
                               |
                    velocity MCP server (FastMCP)
                               |
         plain typed functions (unit-tested without a server)
           source:  get_outline, search_source, read_section, get_exercises
           log:     append_log, read_log
           state:   read_state, update_state
           notes:   read_notes (read-only)
```

- **Core:** tools are plain typed Python functions, exposed through a thin MCP server built on the official `mcp` SDK (`MCPServer`, called FastMCP before mcp 2.0).
- **Front end A, Claude Code:** works with the learner's Claude plan or an API key. `velocity tutor <subject>` regenerates the **tutor workspace** from a template in this repo **on every launch**, then opens Claude Code in it. The workspace contains the tutor `CLAUDE.md` (persona and rules), `.claude/skills/`, `.mcp.json`, permission settings, and hooks. Customizations come from `subject.toml`, never from hand edits; generated files carry a header saying they are regenerated. The tutor never runs from this code repo, which keeps tutoring separate from Claude Code's coding behavior.
- **Front end B, own loop:** a thin agent loop on the `anthropic` SDK with `ANTHROPIC_API_KEY`, using the same MCP server and skills.
- **Authentication:** software distributed to others must not route requests through someone else's claude.ai login. Front end A is Claude Code itself, used with each learner's own login or key. Front end B uses API keys only.

### File locations

| What | Where |
|---|---|
| Global config (vault path, defaults) | `~/.config/velocity/config.toml` |
| Subject config | `<vault>/Tutor/<subject>/subject.toml` |
| Session logs (markdown) | `<vault>/Tutor/<subject>/sessions/YYYY-MM-DD-N.md` |
| Learner state | `<vault>/Tutor/<subject>/state.json` (+ generated `progress.md`) |
| Generated workspaces, OCR cache, session records | `~/.local/share/velocity/` |

The OCR cache stays outside the vault so copyrighted text never syncs into notes or gets indexed by Obsidian.

### Platforms

Linux is the primary platform (the developer's). macOS is supported. Windows is best-effort.

## 4. Milestones

1. **M1: Source ingestion (minimum). Done 2026-11-04.** `get_outline` and a lazy `read_section` for the test book using marker, with a per-page cache shared between sections. A section runs from its heading to the next outline entry's heading, and warns if either is not found. No drop-check repair yet.
2. **M2: Tutor in Claude Code. Done 2026-11-04 (PRs #1-#5).** Rename the package to `velocity`; CI (pytest, ruff). Config files, the MCP server with source and log tools, the workspace template and `velocity tutor <subject>`, logging hooks and the session record, notes write protection, the third-party reminder.
3. **M3: Learner state. Done 2026-11-04.** Per-subject state, session-start summary, spaced-review scheduling.
4. **M4: Skills. Done 2026-11-04** except `/review-notes`, which moves to M5 because it needs `read_notes`. `/quiz` and `/end-session`, then the rest.
5. **M5: Notes. Done 2026-11-04.** `read_notes` with per-subject folder lists and `private: true`; `/review-notes`; note-taking prompts.
6. **M6: Own agent loop. Done 2026-11-05** (`velocity chat`, live-tested with an API key, merged in #13). Front end B on the `anthropic` SDK, with the same MCP server and skills.
7. **M7: Polish and portfolio.** One PR each, in this order: fixes from the M6 live test (done, #15); README with a demo; teaching-quality evals (replaying session records); source improvements (drop-check repair, then non-PDF sources). All done except the eval full run, which the user postponed to limit spending.
8. **M8: Web front end.** A local web UI (`velocity web`) as a third front end on the same core, planned 2026-11-06 (see the decisions log), with the PRs reordered after a design review the same day: (1) server, subject picker and session history (done, #24); (A) session titles and resume/continue for all front ends (done, #25); (B) chat on Claude Code headless (done, #26); (C) chat on the own agent loop, selectable (done, #27); (D) polish: Stop, Retry, a thinking indicator, the reminder on the page (#28). The README demo (screenshots and a video) is postponed until the user can record it (2026-11-09). The license is postponed too (2026-11-09): the user wants the application more fleshed out before publishing one.
9. **M9: Fleshing out (planned 2026-11-09).** One PR each, in this order: a `search_source` tool (done, #29); a source reader beside the web chat and History (done, #30; see the decisions log and "Notes: source reader"); a `view_page` tool that shows the tutor a rendered PDF page, diagrams included, without waiting for OCR (done, #31; see "Notes: view_page"); OCR ahead of time (a chapter or the whole book, in the background, with a clear warning that it takes a while and a strong progress indicator; done, #32; see "Notes: preparing ahead of time"); uploading a source from the web page (a copy kept in velocity's data dir, sent as raw bytes so no new dependency is needed; done, #33; see "Notes: uploading a source"). Compaction moved to the end of M10 (low priority). A tester guide and feedback template come later, when the user invites testers.
10. **M10: Everything from the web page (planned 2026-11-09).** See the decisions log: subjects on the page (done, #34), the sources library (done, #35), a Library tab (in progress, branch `library-tab`; the user's request: see every book and remove unused ones; see "Notes: the sources library"), a sources library, first-run setup and settings, recheck as a button, a clarity pass, then compaction.

### M2 notes (2026-11-04)

- **Claude Code facts the design depends on:**
  - Permission deny rules cannot have allow exceptions, so the tutor is denied Edit and Read on the **whole** vault, plus Bash. Logs are written by velocity's hooks and MCP server, which run outside Claude Code's permissions.
  - `blockWritesOutsideWorkingDirectories` (Claude Code 2.1.314+) blocks writes outside the workspace.
  - Absolute paths in permission rules need a leading `//`.
  - The launcher runs `claude --setting-sources project,local`, and the workspace excludes `~/.claude/CLAUDE.md`, so the user's own Claude Code setup doesn't leak into the tutor.
- **Logging limitation:** the Stop hook uses `last_assistant_message`, because Claude Code's transcript format is internal. Only the tutor's final message in a turn is logged. The tutor prompt asks it to call tools first and put its whole reply last. Blocks from `append_log` are recorded with their own timestamps, so they appear before the reply text of the same turn.
- **Verified end to end** with `claude -p` in a generated workspace:
  - The hooks logged the dialogue word for word.
  - The tutor gave the third-party reminder and the no-sources notice.
  - Forced Write and Edit attempts into the vault were denied by the permission settings, and Bash was unavailable.

### M6 notes (2026-11-04)

- **`velocity chat <subject>`** (`agent.py`): a manual streaming loop on `client.beta.messages.stream`.
  - It uses the manual loop rather than the SDK's Tool Runner because the loop *is* the point: it logs every turn into the session record, injects context, loads skills, streams to the terminal, and accounts for cost.
  - Tools come from velocity's MCP server over stdio (the same server Claude Code uses), plus a local `load_skill` tool.
  - It calls the same hook functions Claude Code's hooks run, so logs, state and recaps behave identically in both front ends.
- **Requests:**
  - Default model `claude-opus-5-5` at effort `medium`, configurable under `[api]` in `config.toml`.
  - Pick the model per run with `--model` and switch during a chat with `/model` (aliases `opus`, `sonnet`, `haiku`, or a full ID). `/model` is handled by the loop and not logged. History is sent unchanged after a switch (the API ignores thinking blocks the new model can't read); the cache is per model, so the first request after a switch rebuilds it. The cost summary is per model.
  - Adaptive thinking (always on for Opus 5.5) and top-level automatic prompt caching.
  - Session context, log annotations and invoked skills arrive as mid-conversation `system` messages, so the cached prefix stays intact.
  - Server-side refusal fallbacks (`fallbacks: "default"`) on models that support them.
  - Tool inputs stream eagerly; unparseable tool JSON is retried.
- **History is append-only.** A turn that fails (API error or refusal) is rolled back to its start, so the history stays valid and its context is re-sent with the next message.
- **Dependency:** `anthropic` is pinned to `>=0.125,<1` because `marker-pdf` requires `<1`. 0.125 supports everything used here.
- **Not yet:** resuming a chat (each `velocity chat` is a new session; the Claude Code front end supports resume), and context compaction for very long sessions.

### M8 notes: web chat on Claude Code (2026-11-06)

- **Experiment first** (two short messages on Sonnet in a scratch vault): one JSON line per message on stdin works; the `UserPromptSubmit` and `Stop` hooks fire for those messages, so the log is written exactly as in `velocity tutor`; `/quiz` works; closing stdin exits with 0 and runs `SessionEnd`; `--resume <claude id>` reattaches to the same velocity session.
- **Engine** (`claude_engine.py`): one `claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages --permission-prompts none` process per chat, in the generated workspace with `velocity tutor`'s arguments. Streamed text deltas become text events; complete assistant messages contribute only their tool calls (their text repeats the deltas); `result` ends a reply.
- **Chats** (`chat.py`): at most one per subject, and none while another session of the subject is open (unless the learner closes it, e.g. after a crash). Pause, the 30-minute idle timeout and server shutdown close stdin, which ends the session without a wrap-up. After `/end-session` the chat stops once the session is wrapped up. Resume uses Claude Code's `--resume` when the session has a Claude Code conversation (found through the hook's id mapping); otherwise `VELOCITY_REOPEN` makes the SessionStart hook reopen the session and asks the tutor to read it first. Continue passes `VELOCITY_CONTINUE`.
- **Page:** a Chat tab (model choice remembered in the browser, skill buttons, End session and Pause, a "?" popup explaining End session, Pause, Resume and Continue) and Resume/Continue buttons in History. Replies stream over server-sent events and are rendered with the same markdown and math code as History. Reloading the page redraws a running chat from the session record.
- **Tests** use `tests/fake_claude.py`, which speaks the same protocol and runs velocity's real hooks at the same moments as Claude Code, so logging, resume, continue and wrap-up are tested without Claude Code.

### Notes: the sources library (M10, 2026-11-09)

- **The Library tab** (asked for by the user, to see every book and clean up): each book once, with its name(s), file, size, prepared pages (PDFs), the subjects that use it, "Add to <current subject>", and a note when it looks like a copy of another (same file name and size; the same book under two file names isn't detected, and doesn't share prepared pages either).
- **Removing from the library** (agreed with the user): only books no subject uses, archived subjects included (otherwise the page says where it's used). A copy velocity made (in `<data>/library/` or an older upload folder) is deleted with its prepared pages, unless another copy shares them; any other file is only forgotten (dropped from the index), never deleted. The confirmation says which ("Deletes velocity's copy (34.1 MB) and its prepared pages" / "Forgets this file. Your file itself stays where it is").
- **A bug the user found:** test PDFs showed up in their library. Tests that didn't set `XDG_DATA_HOME` had written into the real `library/index.json` (since #35); `tests/conftest.py` now isolates every test (#36), and the user's index was cleaned (20 pytest entries removed, a backup kept).

- **What's in it** (`library.py`): every source file of every subject (archived ones too), every file in the library folder (`<data>/library/`, where uploads now go) and in the older per-subject upload folders, and every file ever added (`<data>/library/index.json`, written by `add_source`, `remove_source` and `delete_subject`), so a book stays available after it was removed from every subject or its subject was deleted. Each file is listed once, with the subjects that use it and the names they cite it by.
- **Adding from it:** the "Add a source" dialog offers "From your library" (books this subject doesn't use yet, with where each is used: "Already in Calculus as 'AI Eng'. Prepared pages are shared.") above the upload; it adds the same file, with the name other subjects use as the default. The request names an entry by id (a hash of its path), and only entries in the library can be added.
- **Removing and renaming** (in the subject's settings, applied at once, and `velocity source remove/rename`): only that `[[sources]]` table of `subject.toml` changes; removing keeps the file in the library; renaming notes that earlier logs keep the old name. The reader follows: a renamed book stays open under its new name; a removed one closes.
- **Checked:** in headless Firefox, the user's case: a new subject "AI Engineering", the AI book added to it from the library (no upload, 182 sections), then removed from Calculus (still in the library) and Stewart renamed while open in the reader.

### Notes: subjects on the page (M10, 2026-11-09)

- **Menu:** the subject menu lists active subjects, then "＋ New subject…" and, if any, "Archived (n)…"; choosing one of those opens its dialog and the menu goes back to the subject in use. The page's current subject is a variable (`currentSlug`), not the menu's value. With no subjects, the chat view shows "Create your first subject" and the dialog opens on its own; the Book button is hidden until there is a subject.
- **Settings** (⚙ next to the menu): name (display name only; the folder and ids keep the original slug), extra instructions for the tutor (`persona`), and note folders (chips; a field with the vault's folders as suggestions, leaving out hidden folders and `Tutor/`). Changes apply from the next session.
- **Editing `subject.toml`:** each setting rewrites only its own top-level line (`_set_top_level`), so comments, layout and the `[[sources]]` tables stay as the learner left them; the result is parsed before it is written, and read back to check it. The instructions are written as a multi-line literal string, readable when the file is opened by hand (JSON-style escapes when the text contains `'''`).
- **Archive** is an `archived = true` line (Recover removes it); archived subjects stay in `velocity subjects`, marked. **Delete** is only allowed for archived subjects, after typing the subject's name: it removes `Tutor/<slug>/` (settings, logs, progress) and velocity's records and workspace for it, not the book files. Neither is allowed while a session of the subject is open.
- **Checked** in headless Firefox on an empty vault: first run, create, settings (rename, instructions, a note folder), archive, recover, archive again, and delete after typing the name.

### Notes: uploading a source (2026-11-09)

- **Where:** a ＋ next to the source menu in the reader; a subject without sources opens the reader on an "Add a source" screen (the Book button is always shown now).
- **How:** the file's bytes are the request body (`POST /api/subjects/<slug>/sources?name=…&filename=…`, no form encoding, so no new dependency), written to a `.part` file in chunks, with a 1 GB limit. The copy is kept in `<data>/uploads/<subject>/` (not the vault, not the repo), under the file's base name reduced to safe characters (never a path from the request), with `-2`, `-3`… when taken. Then `add_source` adds it, exactly as `velocity source add` does. Same-origin rules apply as for every request that changes something.
- **Checks:** the type (PDF, EPUB, Markdown, HTML), that the content matches it (a PDF must open in PyMuPDF and have pages, an EPUB must be a zip, text must not be binary), that it isn't empty, and that the source name is free. A refused or failed upload leaves no file behind. The tests found that the first version checked the `.part` file's suffix and so skipped every check.
- **After:** the dialog says how many sections were found, or warns that there's no table of contents; for PDFs it offers "Prepare it now…" (the prepare dialog with the estimate). The tutor uses a new source from its next session, or after pause and resume, since both rebuild the workspace and start a new tool server.
- **Default name:** "Author - Title" file names give the author's surname ("James Stewart - Calculus (8th Edition).pdf" gives "Stewart"); others give the title, shortened at a word. The learner can change it before adding.
- **Other books' outlines** (found when the user uploaded *AI Engineering*, O'Reilly): its chapters are "Chapter 1. Title" with unnumbered sections and a "Summary" in every chapter, and the PDF outline rejected the whole book over the repeated id `summary`. Now "Chapter N." / "Chapter N:" / "CHAPTER N" are chapters too (ids `ch1`, sections `1.summary`, as Stewart's `2.review`), and a repeated id gets `-2`, `-3` like text sources instead of failing (this replaces M1's rule that duplicates are an error; ids stay unique). Stewart's ids are unchanged.
- **Checked** in headless Firefox: the empty screen, then the real 34 MB Stewart PDF uploaded into a new subject (188 sections found), and "Prepare it now…" opening with "1,397 of 1,397 pages, about 2 h 32 min".

### Notes: preparing ahead of time (2026-11-09)

- **Why:** a section's first `read_section` OCRs its pages on the spot (about 6.5 s per page on the user's GPU: §14.3 took 107 s). Preparing does that ahead of time, so reading is instant later.
- **Two ways to start it** (the user's choice: both): `velocity source prepare <subject> --section ch14 | --pages a-b | --all`, with a progress bar in the terminal, and a Prepare… button in the web reader (this chapter, or the whole book), which launches the same command as a detached process (`start_new_session`), so it outlives the page and the server. Output goes to `prepare.log` in the source's cache dir.
- **Warning first:** the dialog says it takes a while, gives the number of unprepared pages and an estimate (30 s to load the models plus 6.5 s per page, measured; Stewart: 1,394 pages, about 2 h 32 min), says the GPU stays busy, and that the tutor keeps working.
- **Progress:** the job writes `prepare.json` (state, pages done of total, the section it's on, time left from the real speed so far) after each batch; the page polls it every 3 s and shows a strip with a progress bar, Cancel, and the outcome (done, stopped, failed with the error, or interrupted if the process died). Sections whose pages are all prepared get a ✓ in the section menu.
- **Batches and the OCR lock:** pages are OCR'd in batches of 10 (about a minute), each under an exclusive file lock (`ocr.lock`, `fcntl.flock`) that `read_section` and `recheck` also take, so only one OCR runs on the GPU at a time. A `read_section` on unprepared pages waits for the batch in progress (the user's choice) and then OCRs only what is still missing. `view_page` and `search_source` never wait.
- **Cancel and resume:** Cancel writes `prepare.cancel`; the job stops after its current batch. Finished pages are kept, and starting again skips them. One job per source at a time.
- **Checked:** a real 3-page run from the command line (28 s with model loading); in headless Firefox with real OCR, a chapter (8 pages, about a minute, ✓ added) and Cancel on Chapter 1 (stopped after the first batch with 10 of 68 pages kept). A test shows `read_section` waiting for the job's batch and OCR'ing only the page the job didn't do.
- **Platform:** the lock uses `fcntl`, so this is Linux/macOS only, like the rest of the OCR setup.

### Notes: view_page (2026-11-09)

- **What:** `view_page(source, page, count=1)` returns a short header (source, PDF pages, the section they're in) and each page as a PNG, up to 3 consecutive pages per call. PDFs only. Rendering takes about 0.1 s per page; no OCR. The tutor may use it whenever it helps (the user's decision): the page the learner asks about, figures (which OCR'd text leaves out), and checking formulas against the printed page.
- **Size:** 1,400 px on the long edge, about 2,250 image tokens per page (pixels / 750; about $0.0045 on Sonnet 5.5 through the API). Chosen on Stewart p. 137: at 1,100 px a limit's subscript (`lim x→3`) was barely legible. PNG, so formulas get no compression artifacts. Current models accept up to 2,576 px.
- **Layers:** `tools.py` returns a plain `PngImage`, and the MCP wrapper turns it into image content (so the tools module doesn't import the MCP server, which would add 0.5 s to every process that imports it). Claude Code shows MCP images to the model directly; the own loop's tool caller now passes text and image blocks inside the `tool_result` (images belong in the result of the tool that produced them).
- **Records** keep the call (name and page), not the images; a rebuilt history shows the placeholder result, and the tutor can look again.
- **Live check** (Claude Code, Sonnet, web session): "About Stewart PDF p. 928 (§14.1): What does the diagram show?" took 12.6 s with one `view_page` call and described both figures correctly. The same question before took 91 s (OCR of §14.1) and failed, because the figures weren't in the OCR'd text.

### Notes: source reader (2026-11-09)

- **Server:** `GET /api/subjects/<slug>/sources` (names, kinds, outlines; cached per file and modification time), `…/sources/<name>/file` (PDFs only, with range requests: 206 for a 64 KB slice of the 34 MB Stewart), `…/sources/<name>/sections/<id>` (text sources only). Files are found by source name through `subject.toml` (`tools.source_ref`, shared with the tutor's tools); a request never carries a path.
- **Page** (`web_static/reader.js`): PDF.js 6.4.299's `PDFViewer` in an absolutely positioned container, pages fitted to the panel's width and re-fitted when the panel resizes. Its `pagechanging` event drives the page box, the section menu and "Ask about this page". Citations are linked after markdown is rendered, by walking text nodes (not inside code or math); a section id that isn't in the outline isn't linked, and trailing punctuation stays outside the link.
- **Layout:** with no session open on the Chat tab, the book takes the middle of the page on its own (the user's request: there is nothing to sit beside); the Chat tab goes back to the start panel. Otherwise it sits to the right of the chat or the History log.
- **The tutor** is told in web sessions with sources that its citations become links (`WEB_UI_ENV` / `web_ui`, which replaced the reminder-only flag from PR D).
- **Speed, measured on the user's session:** model turns take 3–5 s; the wait was OCR on a section's first read (about 6 s per page: §14.3 took 107 s, §14.1 91 s). Hence `view_page` and OCR ahead of time next.
- **Testing the page:** headless Firefox driven over WebDriver BiDi (`firefox --headless --remote-debugging-port`, a short Python script with `websockets`, run through `uv run --with`), with screenshots. It found the bug where the Book button (styled as a tab) also triggered the view switch and hid both views, and a page not re-fitted after the panel narrowed.

### Notes: source search (2026-11-09)

- **Why:** the tutor used to guess section numbers or read whole sections to find a topic (thousands of tokens). `search_source` runs locally and returns about 100–450 tokens; one search plus one targeted `read_section` is cheaper than guessing.
- **What is searched** (`search.py`, `search_chunks()` on both source types): for PDFs, the text layer of every page, mapped to the outline entry the page falls under (pages before the first entry are skipped). It is cached as `text-layer.json` in the source's cache dir, since extracting it takes about 3.5 s for a 1,400-page book; later searches take about 0.15 s. Pages without a text layer (scans) use their OCR'd text if cached. Text sources are searched section by section.
- **Matching:** keywords, case-insensitive, substrings ("limit" finds "limits"); words under 3 letters are dropped. A section matches if its pages together contain every word; otherwise the best partial matches are returned and labelled so. No embeddings (they would need a model and new dependencies).
- **Text-layer flaws found in Stewart:** math is garbled (`f(x)` is `fsxd`), so formulas can't be searched for. The fonts lose the "f" of ligatures ("definition" is "deinition", "flux" is "lux", "coefficient" both "coeficient" and "coeicient"), so each query word is also tried in those forms; Unicode ligature characters (ﬁ) are normalized with NFKC.
- **Ranking:** occurrences capped at 3 per word per page (so index pages don't dominate), the whole phrase weighted 10x, and a boost when the section title contains the query. On 12 test queries on Stewart, the top result was the section that teaches the topic every time; the index still appears lower down.
- **Result format:** one line per section, with the best page (the snippet's) first: `Stewart §2.3: … (best match PDF p. 133; also on pp. 129, 131, …): <snippet>`. Before the best page was named, the tutor said it couldn't give an exact page.
- **Live check** (Claude Code, Sonnet): asked where the squeeze theorem is explained, the tutor made one `search_source` call and answered §2.3, PDF p. 133, plus §11.1 and Appendix F.

### M8 notes: web chat on the own loop (2026-11-09)

- **Engine interface** (`engine.py`): `start`, `send` (streams `ChatEvent`s ending with `done` or `error`), `stop`, `running`. `ChatManager` drives Claude Code (`claude_engine.py`) or the own loop (`api_engine.py`) through it.
- **Own-loop engine** (`api_engine.py`): the `velocity chat` agent with an event sink in place of the terminal (both can be set). The MCP server runs over stdio from a background task that holds the connection until the chat stops, because the MCP client must be entered and left in the same task. `/model`, `/exit` and unknown commands are answered on the page without an API request. If the page goes away mid-reply, the turn is cancelled and rolled back, as for an API error.
- **Rebuilt history** (`history.py`): `HistoryBuilder` (shared with the evals) and `rebuild_messages(session, tool_names)`. Calls to tools the agent lacks are left out, since they would make the request invalid. The SessionStart context says the conversation is restored but tool results aren't.
- **Record events:** `engine` (web starts and resumes: engine and model; written once the session is open, which for Claude Code is after its SessionStart hook) and `usage` (each own-loop turn: tokens, model, cost estimate, written before the Stop hook so a wrap-up turn counts too). Neither appears in the markdown log.
- **Resume on Claude Code after the own loop:** Claude Code's own conversation lacks the own loop's turns, so `--resume` is used only when Claude Code ran the session last; otherwise the session is reopened and the tutor reads it.

### M7 notes: non-PDF sources (2026-11-06)

- **`TextSource`** (`text_source.py`) reads Markdown, HTML and EPUB with the standard library only (`html.parser`, `zipfile`, `xml.etree`). The file becomes markdown in memory; no cache, since parsing a whole book takes well under a second. It has the same `outline()` / `read_section()` interface as `PdfSource`; sections have no page numbers, so the tools leave them out.
- **Outline:** Markdown and HTML use their headings (ATX and setext; not inside code fences). EPUB uses the book's own table of contents (the EPUB 3 `nav`, else the EPUB 2 NCX), resolved to positions in the text through element ids, and falls back to headings. A section runs to the next outline entry, as for PDFs.
- **Section ids:** the number for numbered titles (`2.4`), `ch<n>` for chapters (also Roman numerals: "CHAPTER IV." is `ch4`), `part-<n>`, `app-<x>`, otherwise a slug of the title; repeats get `-2`, `-3`.
- **HTML to markdown:** headings, paragraphs, lists, emphasis, code, tables (as rows), image alt text. Math: LaTeX in the text passes through; MathML becomes its TeX annotation or alt text; an image with a `data-tex` attribute (MathJax-rendered math, as in Project Gutenberg's EPUBs) becomes its LaTeX.
- **Exercises:** `get_exercises` also accepts an emphasized label paragraph ("*Exercises I.*"), not only an "Exercises" heading.
- **Tested on a real book:** *Calculus Made Easy* (Thompson, 1914; Project Gutenberg #33283, EPUB 3, 13 MB). 37 entries from its table of contents in 0.16 s, every formula as LaTeX, and `get_exercises(ch4)` returns Exercise Set I complete.

### M7 notes: dropped-text check (2026-11-06)

- **Detection** (`dropcheck.py`), as decided in the M1 findings, but without book-specific font names: every horizontal text-layer line with a prose word (spans in math or symbol fonts don't count) must lie inside a marker block that produced text. Lines in headers, footers and figures are skipped. marker's block positions are in PDF points, so they compare directly with the text layer. Missed lines are grouped into regions: by their empty block, or, outside any block, by adjacency.
- **Repair:** all regions of one OCR run are re-OCR'd in a single marker call, each as a tightly cropped page (1.5 pt above and below; with 6 pt, marker read the neighbouring line instead). Recovered text goes in after the block that precedes it in marker's reading order, found in the page markdown by that block's text (letters and digits only). Headings in recovered text become bold, so they aren't mistaken for section boundaries. If re-OCR returns nothing, the text-layer text is used with a note that its math may be garbled, and `read_section` warns the tutor.
- **Per-page report:** `pages/<n>.check.json` lists each region and how it was repaired; its absence marks a page cached before the check. `velocity source recheck <subject>` OCRs such pages again.
- **Result on Stewart §2.4 (PDF pp. 136–146):** 5 regions found, all real: the 4 known drops (including the key clause of Definition 7) and one more on p. 146 (the start of Definition 1 in §2.5), outside M1's range. All 5 were restored by re-OCR, in the right place, with LaTeX and ligatures intact. There were no false positives. It added about 3 seconds to the 90-second run.
- **Fixes after the first recheck of the user's cache (25 pages, 18 regions):**
  - A region now also takes in adjacent uncovered math-only lines (a limit's subscript, the end of a formula), which the prose-only crop cut off: exercise 38 on p. 135 ended at "lim g", and 39 lacked "= 0".
  - Regions sharing an anchor were inserted in reverse order (37, 39, 38). They are now inserted last to first.
  - marker's result on small crops depends on the exact crop: the grown crop of "Find the value of c so that lim B(t) exists" came back empty, while the prose-only crop was read perfectly, subscript included. Empty results are now retried on the prose-only crop before falling back to the text layer.
  - On §2.3 (pp. 127–136): 11 regions, 9 restored by re-OCR in the right place, and 2 text-layer fallbacks (both empty on both crops; the tutor is warned).
  - `velocity source recheck --all` re-OCRs every cached page, not only unchecked ones.
- **Blind spot (unchanged):** a block with partial output can still hide a dropped line, because the line counts as covered.

### M7 notes: evals (2026-11-05)

- **Next-turn replay.** A case is a recorded session cut off right after one learner message (or a hand-written history), plus expectations for the tutor's next turn. The harness (`evals/`, run with `uv run python -m evals.run`) rebuilds that moment in a throwaway vault and runs one turn on the real `velocity chat` agent and tools, through an in-process MCP server. Replay rather than scoring old transcripts, because records don't contain tool calls, and replay lets models and prompts be compared on the same cases.
- **Rebuilding the moment:**
  - The earlier part of the session goes into the agent's history, the session record and the markdown log, including tutor blocks and learner annotations. The session-start context is regenerated, with the earlier sessions' records copied in.
  - Learner state is rebuilt from the final `state.json` by replaying questions and threads from earlier sessions. Dates shift so the session's day becomes today, which keeps the same reviews due. Topic status comes from the final state.
  - The record doesn't say when in a session a question or thread was recorded, so a case states how many of its own session's questions and threads already existed (`session_state`). Without it, the dry run showed Sonnet recording a question a second time. Threads opened in the current session stay open.
  - Known gap: the Claude Code front end only logged the tutor's final message per turn, so some history is shorter than what the tutor said.
- **Dry run (2026-11-05):** 3 cases on Opus and Sonnet cost $0.25 ($0.058 per case on Opus, $0.026 on Sonnet), so a full run of 38 cases is about $3–4 before the judge. It led to two harness fixes: the citation pattern now accepts words between "Stewart" and "§x.y", and the in-session state above.
  - Sources: PDF paths come from the user's velocity config. The OCR cache is reused read-only; an uncached section returns a tool error instead of starting OCR. Cases whose source is missing are skipped.
- **Cases** (`evals/cases/*.json`): 28 from the 8 recorded calculus sessions (committed with the user's permission; the test vault path was replaced) and 10 hand-written ones for gaps (`/explain-differently`, `/assign-problems`, `/review-notes`, "just tell me", a confidently wrong claim, a subject without sources, the tutor's own error).
- **Code checks** per case: tools that must or must not be called (`load_skill:<name>` for skills), and regexes the reply must or must not match (reminders, citations, not giving away an answer). Each case also says what the LLM judge (PR 2) should look for.
- **Runs:** Opus 5.5 and Sonnet 5.5 by default, with a spending limit (`--budget`, default $5; no new case starts once it is reached). Results go to `evals/runs/` (gitignored).

### M7 notes: the judge (2026-11-06)

- **Judge** (`evals/judge.py`): Opus 5.5 by default (`--judge sonnet` or `none`), effort `medium`, structured output (a JSON schema), refusal fallbacks. It sees what the tutor saw (conversation so far, annotations, the learner's message), the tool calls of the turn, and the reply.
- **Rubric:** six criteria, each graded pass, fail or n/a, with the reason written before the verdict: asks before telling, hints before answers, correct, honest feedback, clear next step, and the case's own focus. Pass/fail rather than a 1–5 scale, because binary verdicts are more consistent and easier to check against human labels.
- **Self-preference:** Opus judges Opus's own replies, which can bias it upward. The hand labels measure this: agreement is reported per model as well as overall.
- **Judge dry run (2026-11-06):** the same 3 cases cost $0.40 ($0.26 tutor, $0.14 judge, about $0.023 per judged reply). The judge failed Opus on "asks before telling" for explaining encryption before asking what the learner knew, which matched a human read, and used n/a sensibly. A full run is estimated at $5–6, right at the $5 limit; the user postponed it.
- **Known gap: earlier tool calls aren't in the replayed history.** Session records store what the tutor said, not its tool calls, so a replayed tutor can't see that it already recorded a question in an earlier turn. Sonnet re-recorded quiz question 1 in `2026-11-05-1-t5` for this reason. No check penalizes it. **Fixed for new sessions (2026-11-06):** session records now store each tool call's name and input (not its result, which can contain source text) as a `tool` event, through a `PostToolUse` hook in Claude Code and directly in `velocity chat`. A replay puts these calls into the tutor's history (with a placeholder result) and runs the state-changing ones again, so the rebuilt state matches. The committed records predate this and keep using `session_state`.
- **Labels** (`evals/label.py`): `export` samples replies from a run (alternating models, fixed seed) into a Markdown file for Obsidian, hiding the model and the judge's verdicts; `agree` reports agreement and Cohen's kappa per criterion and lists every disagreement with the judge's reason. Labels are committed in `evals/labels/`.

### M3 notes (2026-11-04)

- **Decisions:** what is tracked, who writes it, format and location (see §2.4).
  - The tools are small and typed rather than one generic `update_state`, because narrow tools are easier for the model to call correctly.
  - Log annotations reach the tutor automatically with the learner's next message (PR #7), after the first real session showed that switching windows to mention them was friction.
- **Launcher isolation, found while testing:**
  - Account-level MCP connectors (e.g. claude.ai integrations) were loaded into the tutor, so the launcher now passes `--strict-mcp-config --mcp-config <workspace>/.mcp.json`.
  - Project `allow` rules only apply after the workspace is trusted, so velocity's tools are also pre-approved with `--allowedTools mcp__velocity`.
- **Model behavior:**
  - In real runs, Sonnet called `record_question` after the learner answered, with a sensible topic, outcome and note. It also opened a thread for the misconception on its own.
  - Haiku did not call it. The tutor needs a capable model; this belongs in the README (M7).

### M1 findings (2026-11-02)

Test book: Stewart, *Calculus: Early Transcendentals*, 8th ed. (1,397 PDF pages), at `sources/mathematics/` (gitignored).

- **Outline: solved.** The PDF has an embedded TOC (188 entries, chapter > section, titles like `3.4: The Chain Rule`) readable with `pymupdf`'s `get_toc()`. Strip stray `\x00` from titles. Implemented in `outline.py`.
- **Page numbers:** the TOC gives PDF page numbers; printed page numbers differ by an offset of 32 (PDF p.136 = printed p.104). Citations must state which they use.
- **Equations: plain text extraction is unusable.** The math font has a broken encoding: `f(x)` becomes `fsxd`, `=` becomes `−`, `∫` becomes `y`, `→` becomes `l`, and `fi` ligatures drop (`deinition`). `pymupdf4llm` gives the same garbled symbols.
- **Environment constraints:** the machine has an AMD GPU (RX 7700 XT), so `torch` is pinned to the CPU build to avoid downloading CUDA libraries. `marker-pdf` pins an old `pillow` with no Python 3.14 wheel, so the project is pinned to Python 3.12.

### M1 findings: marker-pdf evaluation (2026-11-04)

Tested on all of Section 2.4 (PDF pp. 136–145, plus neighbouring 2.3 exercises and the start of 2.5).

- **Setup:** `torch`/`torchvision` must be listed directly in the `ocr` group, because `tool.uv.sources` only applies to direct dependencies (transitively they resolved to CUDA builds). Without an NVIDIA GPU, `surya` (marker's OCR engine) runs its recognition model through llama.cpp's `llama-server`. It uses the official Vulkan release build, unpacked to `~/.local/opt/llama.cpp/b12118/` and pointed to by the `LLAMA_CPP_BINARY` env var. This runs on the AMD GPU.
- **`--force_ocr` is required.** By default marker keeps the PDF text layer for prose and only OCRs equation regions, so inline math stays garbled. With `--force_ocr`, both inline and display math come out as clean LaTeX, ligatures are restored, and reading order (two-column exercises, sidebars, `##` section headings) is correct.
- **Speed:** about 7.3 s/page on the GPU once models are loaded (11 pages in 81 s), or roughly 3 h for the whole book.
- **Failure mode: silently dropped text.** In 11 pages, 5 prose passages were missing. Usually marker detected the block but produced empty output; once (a margin note) it never detected the block. One of them is the key clause of Definition 7. Text-based comparisons against the PDF text layer can't catch these reliably: order-sensitive diffs flag reordered columns, and order-insensitive n-gram checks miss drops whose wording repeats nearby (Definitions 6 and 7 are near-identical).
- **Cosmetic:** figure captions run into the next paragraph (`FIGURE 1Note that`), consecutive exercises sometimes lose their line breaks, end-of-proof ■ is sometimes lost, and ε appears as both `\epsilon` and `\varepsilon`. Figures are saved as separate images; text inside them is not transcribed.
- **Decision: marker with `--force_ocr`, plus a spatial dropped-text check (M7, built 2026-11-06; see "M7 notes: dropped-text check").** Every prose-font line in the PDF text layer (fonts `TimesLTStd-*` / `MyriadPro-*`, excluding the header strip) must fall inside a marker JSON block with non-empty output. On Section 2.4 this flagged all 5 drops with 0 false positives. Blind spot: partial drops inside a block that has some output. Repair: re-OCR the flagged block's cropped region; if still empty, fall back to the text-layer text with a "[math may be garbled]" marker. A vision-model repair is possible once front end B exists.

## 5. Decisions Log

- **2026-11-09 (M10: everything from the web page):**
  - Goal: the web page can do everything the terminal can, so every user can use velocity without a terminal.
  - **Subjects stay**, as free-form groups the learner names and organizes as they like ("Calculus", "AI Engineering", "Work notes"): each keeps its own sources, logs, progress, note folders and instructions. The page can create subjects, edit their settings (name, extra instructions, note folders), and **archive** them (hidden, everything kept, recoverable). **Delete** is only offered for archived subjects, after typing the subject's name; it removes the subject's folder in `Tutor/` and velocity's records for it, but not the book files.
  - **A sources library:** books added to any subject can be added to others from the library, without uploading or OCR'ing them again (the OCR cache is per file, so prepared pages are shared). Sources can be removed from a subject and renamed.
  - Order, one PR each: subjects on the page; the sources library; first-run setup and settings (vault, OCR program, API defaults); recheck as a button; a **clarity pass** over the whole page (explain through layout and short labels rather than text); compaction last (low priority).

- **2026-11-09 (M9: the source reader):**
  - A panel to the right of the chat (and of the History log), with a draggable divider; a Book button opens and closes it. It remembers its width and the last source and page per subject. Closed, the page looks as before.
  - It opens three ways: the Book button (source picker and outline), a citation like `Stewart §2.4` in a reply or log, and a page reference like `PDF p. 133`.
  - PDFs show the real pages; text sources show the section's text rendered like the chat. No OCR'd-text view of PDFs for now.
  - "Ask about this page" puts `About <source> PDF p. <n> (§<id>): ` at the start of the message box; the learner types the question after it, so the tutor sees it as the learner's own words, on both engines.
  - **PDF.js** (Mozilla, `pdfjs-dist` 6.4.299 from jsDelivr, approved by the user as a new front-end library) draws the PDF, because the page must know which page the learner is on. Firefox's built-in viewer can be told to go to a page (changing the frame's `#page=` jumps instantly, tested by the user in Firefox), but the page can't read where the learner scrolled (cross-origin). PDF.js is loaded only when the reader opens, and fetches the PDF in pieces (HTTP range requests).
  - The server sends a source file only by source name, looking up its path in `subject.toml`; no path ever comes from a request.

- **2026-11-09 (M8, PR D: web polish):**
  - In the web UI the third-party reminder moves from the tutor to the page: it shows where sessions start (the start panel, and before Continue), and the session-start context tells the tutor the learner has seen it. The terminal front ends keep the tutor's reminder.
  - A Stop button ends a streaming reply. The partial reply is kept: it is logged with a *(stopped)* mark and stays in the tutor's history. Claude Code is stopped with a stream-json `interrupt` control request (verified 2026-11-09; its Stop hook doesn't run, so velocity logs the partial reply). The own loop cancels the request.
  - A "thinking…" indicator until the first text or tool call, and a Retry button on a failed reply. A retried message is not logged a second time.
  - Messages can't be sent while a reply streams (unchanged).
  - The README demo uses screenshots plus a short video (GitHub plays uploaded videos with controls, so it can be paused; a GIF can't).
  - A phone-width layout is not needed yet; it is noted under future directions.

- **2026-11-09 (M8, PR C: web chat on the own loop):**
  - The web page offers both engines: Claude Code (the default; the learner's plan) and the own loop (API key, billed per token). The page remembers the last engine and model for new sessions. Without `ANTHROPIC_API_KEY` the own loop is greyed out with a hint.
  - The own loop resumes any paused session, whichever front end started it, by **rebuilding its history from the session record**: learner and tutor messages, blocks, and tool calls with a placeholder result (results aren't recorded because they can hold source text). The rebuild code moves from the evals into the core, and the evals use it from there. Claude Code can't take a rebuilt history, so it keeps `--resume` or the reopen path.
  - Resume defaults to the engine and model that ran the session last, shown preselected in a dialog so switching is one click. Switching to the own loop warns that the first reply re-sends the session so far. If the last engine can't run (no API key, or no `claude`), the other one is preselected with the reason. Each web start and resume records its engine and model in the session record; older sessions are inferred (a Claude Code conversation mapping means Claude Code, otherwise the own loop).
  - Cost (own loop only): a running session total in the chat bar, plus a small figure for the cost of each reply. Each own-loop turn records its token usage and cost estimate in the session record, so the total survives reloads and resumes. The "no dollar amounts" rule still applies to the Claude Code engine.

- **2026-11-06 (sessions: titles, resume, continue; from an M8 design review):**
  - Logs stay one sitting, so no file grows without limit. Interrupted sessions resume in place; wrapped-up sessions stay final and are *continued* in a new, linked session (`continues:` property, earlier recap in the start context).
  - The tutor titles each session (`set_session_title`), so past sessions can be found by topic.
  - Exception to "logs are append-only": velocity updates the `title:` line of the log's properties block. It is metadata Obsidian edits too, and the log check already ignores the header. The dialogue is never rewritten.
  - `--continue <id>` for `velocity tutor` and `velocity chat` (through the `VELOCITY_CONTINUE` environment variable for Claude Code's SessionStart hook, and the hook payload for the own loop), and `velocity sessions <subject>`.
  - Existing sessions stay untitled; no backfill.
- **2026-11-06 (M8 planning):**
  - The web UI drives both engines, selectable: the own agent loop (API key, billed per token) and Claude Code run headless (`claude -p --input-format stream-json --output-format stream-json --include-partial-messages`), which uses the learner's own plan through their own Claude Code install.
  - v1 has chat and session history. Progress view and source reader may come later.
  - Stack: Starlette and uvicorn (already installed through `mcp`, now listed directly) and a plain HTML/JS page with KaTeX, marked and DOMPurify from jsDelivr at pinned versions. No build step.
  - For both daily use and as a portfolio demo.
  - Safety: listens on 127.0.0.1 only; the Host must be the local server (against DNS rebinding); requests that change state must come from the page's own origin. The vault is only read by the history view.

- **2026-11-05 (M7 planning):**
  - M7 covers the README, teaching-quality evals and source improvements, in that order, one PR each.
  - A web front end becomes M8. The MIT license comes after M8, not at the end of M7.
  - Teaching-quality evals replay the JSONL session records and combine **code checks** for hard rules (cites sources when it has them, calls the state tools, never writes notes) with an **LLM judge** scoring teaching quality against a rubric (asks before telling, checks understanding). The details are planned with the user before building, since runs cost API money.
- **2026-11-02:** Python 3.12 (ML dependencies lack 3.14 wheels), `uv`, `pytest`, `ruff`.
- **2026-11-04:** OCR with `marker-pdf --force_ocr`, ingested lazily per section and cached; spatial drop check with crop re-OCR repair (deferred to M7).
- **2026-11-04 (design interview):**
  - The product is a subject tutor; sources are optional.
  - It is for anyone, local-first, and each user brings their own key; no hosting.
  - It is a portfolio project for entry-level AI roles.
  - The model is Claude: through Claude Code first (the learner's plan or an API key), and through the `anthropic` SDK with an API key in M6 (`velocity chat`, default `claude-opus-5-5`).
  - The architecture is an MCP core with two front ends, built on the `mcp` SDK (FastMCP).
  - The interface is a CLI (Claude Code) with session logs rendered in Obsidian.
  - Notes: per-subject folder lists, `private: true` opt-out, read-only enforced by permissions, a hook and a test, and a third-party reminder at every session start.
  - The name is `velocity`.
- **2026-11-04 (M3):** Learner state is stored in `state.json` per subject in the vault, with a generated `progress.md`. The tutor updates it during sessions through small typed tools. Reviews double the interval on correct answers, keep it on hinted ones, and reset it on wrong ones.
- **2026-11-04 (M2):**
  - Session record: one JSONL file per session at `~/.local/share/velocity/records/<subject>/<session-id>.jsonl`, one event per line (timestamp, role `learner`/`tutor`/`block`/`event`, text).
  - Session ids are `YYYY-MM-DD-N`.
  - Markdown log: a properties block, an H1, then `**You:**` / `**Tutor:**` turns, with tutor-added blocks as Obsidian callouts (`> [!example] Problem 1`) and a footer saying how the session ended. It is rendered once per turn, sorted by timestamp, so blocks the tutor adds mid-turn land in the right place.

## 6. Open Questions

- None at the moment.

## 7. Future Directions

- **Web UI on a phone:** a layout for narrow screens, so `velocity web` works from a phone on the same network. This needs more than CSS: the server listens only on 127.0.0.1, so reaching it from another device would need an explicit opt-in and some form of authentication.

- **Rubicon:** a separate future project, a knowledge-base overseer that finds connections and tensions in notes, quizzes recall, and suggests vault restructuring. It would reuse the notes MCP tools from this project, and would only ever **propose** changes for the learner to approve, never edit notes directly.
- Name alternatives considered: Marginalia, Whetstone, Lantern, Maieutic.

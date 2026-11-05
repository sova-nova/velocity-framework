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
- A custom GUI. Obsidian renders the session log; a UI may come later.
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
- The tutor only **appends**. It never rewrites earlier content, and it treats past sessions' logs as read-only.
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
- Every session starts with a reminder that notes and logs the tutor reads are sent to a third-party API.
- The tutor prompts note-taking at natural moments, and **critiques** notes on request (gaps, misconceptions, missing connections).
- Optional flashcards, ideally written by the learner and reviewed by the tutor.

### 2.6 Sources and ingestion

Naive chunk-and-embed RAG is a poor fit for teaching, because textbooks have structure the tutor should use.

1. Extract the table of contents and build an outline with stable section IDs (`2.4`, `ch2`, `2.review`, `app-f`, `preface`).
2. Extract section text on demand (lazily, on first `read_section`), with math as LaTeX, and cache it.
3. Later: extract problem sets separately so the tutor can assign exercises; ingest a solution manual if available.
4. Later: non-PDF sources (markdown, HTML, EPUB) behind the same interface.

Source tools: `get_outline()`, `read_section(id)`, and `get_exercises(section_id)`, which returns the section text from its EXERCISES heading on. `search(query)` comes later.

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
           source:  get_outline, read_section, (search, get_exercises)
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
6. **M6: Own agent loop.** Front end B on the `anthropic` SDK, with the same MCP server and skills.
7. **M7: Polish and portfolio.** MIT license (added once the project is finished, decided 2026-11-04), README with a demo, teaching-quality evals (replaying session records), drop-check repair, non-PDF sources.

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
- **Decision: marker with `--force_ocr`, plus a spatial dropped-text check (M7).** Every prose-font line in the PDF text layer (fonts `TimesLTStd-*` / `MyriadPro-*`, excluding the header strip) must fall inside a marker JSON block with non-empty output. On Section 2.4 this flagged all 5 drops with 0 false positives. Blind spot: partial drops inside a block that has some output. Repair: re-OCR the flagged block's cropped region; if still empty, fall back to the text-layer text with a "[math may be garbled]" marker. A vision-model repair is possible once front end B exists.

## 5. Decisions Log

- **2026-11-02:** Python 3.12 (ML dependencies lack 3.14 wheels), `uv`, `pytest`, `ruff`.
- **2026-11-04:** OCR with `marker-pdf --force_ocr`, ingested lazily per section and cached; spatial drop check with crop re-OCR repair (deferred to M7).
- **2026-11-04 (design interview):**
  - The product is a subject tutor; sources are optional.
  - It is for anyone, local-first, and each user brings their own key; no hosting.
  - It is a portfolio project for entry-level AI roles.
  - The model is Claude: through Claude Code first (the learner's plan or an API key), and through the `anthropic` SDK with an API key in M6.
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

- Teaching-quality evaluation design. **Deferred to M7**; session records from M2 on are its raw material.

## 7. Future Directions

- **Rubicon:** a separate future project, a knowledge-base overseer that finds connections and tensions in notes, quizzes recall, and suggests vault restructuring. It would reuse the notes MCP tools from this project, and would only ever **propose** changes for the learner to approve, never edit notes directly.
- A local web UI or desktop app if the Obsidian workflow feels limiting.
- Name alternatives considered: Marginalia, Whetstone, Lantern, Maieutic.

# Tutor Harness: Design Document

Status: draft v0.1 (pre-implementation)

## 1. Purpose

An LLM harness built specifically to make an LLM act as a tutor or professor. It teaches lessons, asks questions, runs quizzes, and answers questions, using a user-supplied textbook or other resource as its source material. Every session produces a log that serves both as a study artifact for the learner and as persistent context for the agent.

### Goals

- One-on-one tutoring dialogue grounded in a specific resource (e.g., a textbook PDF).
- A session log the agent writes and the learner can read, annotate, and revisit.
- Persistent learner state so the agent knows what has been covered, what went poorly, and what is due for review.
- Learner-owned note-taking, supported but never replaced by the agent.
- A skills system so the learner can invoke specific teaching behaviors on demand (e.g., `/quiz`, `/socratic`).

### Non-goals (for now)

- Training or fine-tuning models.
- Multi-user or hosted deployment.
- Automatic grading of free-form proofs or long-form work beyond LLM judgment.
- A polished GUI. A CLI or minimal local interface is enough for v1.

## 2. Core Concepts

### 2.1 The tutor

A single system prompt defines the tutor persona and pedagogy. Defaults:

- Diagnose what the learner already knows before teaching.
- Ask before telling; give hints before answers.
- Interleave earlier material into new questions (retrieval practice).
- Be honest about errors, including its own. No agreeing for the sake of comfort.
- Keep explanations tied to the source material, with citations to section or page.

The persona should be swappable per subject (e.g., a math tutor and a security tutor may behave differently).

### 2.2 The session log (two layers)

| Layer | Format | Owner | Purpose |
|---|---|---|---|
| Structured state | CSV, JSON, or sheet | Agent writes, learner may read | Topics covered, quiz results, confidence ratings, weak spots, next-review dates. Loaded at session start. |
| Narrative log | One markdown file per session | Agent writes, learner annotates | What was taught, questions asked, mistakes, open threads. |

The log doubles as the context indicator. At session start the agent loads a compact summary of the structured state rather than the full history, which keeps context small across many sessions.

### 2.3 Learner-owned notes

Learning benefits from effortful retrieval and from restating ideas in one's own words, so the agent must not write the learner's notes for them. The harness supports note-taking without replacing it:

- A notes file or pane owned by the learner. The agent has **read-only** access.
- The agent's log and the learner's notes are separate artifacts.
- The agent prompts note-taking at natural moments ("Summarize that in your own words before we move on").
- The agent **critiques** notes on request: gaps, misconceptions, missing connections.
- Optional flashcard generation, ideally with a mode where the learner writes the cards and the agent reviews them.

### 2.4 Source material (textbooks and resources)

Naive chunk-and-embed RAG is a poor fit for teaching. Textbooks have structure the agent should use.

Ingestion pipeline:

1. Extract the table of contents and build an outline: chapters, sections, exercises.
2. Store section text keyed by stable IDs.
3. Extract problem sets separately so the agent can assign exercises and check answers. Ingest a solution manual if available.
4. For math-heavy PDFs, plan for a fallback: render pages to images for a vision-capable model, or use an OCR tool built for math. Plain text extraction mangles equations.

Agent tools over the source:

- `get_outline()`
- `read_section(id)`
- `search(query)`
- `get_exercises(section_id)`

### 2.5 Skills

A skill is a folder containing a `SKILL.md`: a YAML header (`name`, `description`) followed by plain-language instructions, optionally with scripts or reference files.

Progressive disclosure: only names and descriptions are loaded into the system prompt up front. The full skill text loads when the skill is invoked or judged relevant.

Harness implementation:

1. Scan `skills/` at startup.
2. Inject names and descriptions into the system prompt.
3. Provide a `load_skill(name)` tool, and map slash commands (`/quiz`) to the same files.

Planned initial skills:

| Skill | Behavior |
|---|---|
| `/quiz` | Short quiz on recent material, one question at a time, hints before answers, results logged |
| `/socratic` | Guide the learner to the answer without giving it |
| `/explain-differently` | Re-explain the last concept with a different angle or analogy |
| `/review-notes` | Critique the learner's notes (read-only) |
| `/assign-problems` | Pull exercises from the source, assign, and check |
| `/end-session` | Update log and state, schedule reviews, prompt for notes |

## 3. Architecture (v1 sketch)

```
Learner (CLI)
    |
Harness loop  <->  LLM API
    |
    +-- Tools
    |     +-- source:   get_outline, read_section, search, get_exercises
    |     +-- log:      append_log, read_log
    |     +-- state:    read_state, update_state
    |     +-- notes:    read_notes (read-only)
    |     +-- skills:   load_skill
    |
    +-- Data
          +-- sources/   (ingested textbooks)
          +-- sessions/  (narrative logs)
          +-- state/     (structured learner state)
          +-- notes/     (learner-owned)
          +-- skills/    (SKILL.md folders)
```

Tools start as plain functions in a single process. They can be exposed as MCP servers later if modularity or reuse across other agent profiles becomes useful.

## 4. Milestones

1. **M1: Source ingestion.** Pick one textbook. Extract the outline and section text; implement `get_outline` and `read_section`. Evaluate extraction quality, especially for equations.
2. **M2: Tutor loop.** Minimal CLI chat with the tutor prompt and source tools. Append-only session log.
3. **M3: Learner state.** Structured state file, session-start summary, spaced-review scheduling.
4. **M4: Skills.** Skill loader plus `/quiz` and `/end-session`; then the rest.
5. **M5: Notes support.** Read-only notes access, `/review-notes`, note-taking prompts.
6. **M6: Polish.** Persona swapping, better interface, evaluation of teaching quality.

### M1 findings so far (2026-11-02)

Test book: Stewart, *Calculus: Early Transcendentals*, 8th ed. (1,397 PDF pages), at `sources/mathematics/` (gitignored).

- **Outline: solved.** The PDF has an embedded TOC (188 entries, chapter > section, titles like `3.4: The Chain Rule`) readable with `pymupdf`'s `get_toc()`. Strip stray `\x00` from titles. Use it for section IDs; no parsing needed.
- **Page numbers:** the TOC gives PDF page numbers; printed page numbers differ by an offset of 32 (PDF p.136 = printed p.104). Citations must state which they use.
- **Equations: plain text extraction is unusable.** The math font has a broken encoding: `f(x)` becomes `fsxd`, `=` becomes `−`, `∫` becomes `y`, `→` becomes `l`, and `fi` ligatures drop (`deinition`). Example: `tsxd −y x a fstd dt` for g(x) = ∫ₐˣ f(t) dt. `pymupdf4llm` (a transitive dependency of `pymupdf`) gives the same garbled symbols, so it does not help.
- **Chosen next step: local math OCR with `marker-pdf`**, tested first on one section (2.4, the epsilon-delta definition) and compared against the page image. Fallback if quality or speed is poor: render pages to images and transcribe with a Claude vision model (needs an API key in an env var, costs per page).
- **Environment constraints:** the machine has an AMD GPU (RX 7700/7800 XT), so `torch` is pinned to the CPU build to avoid downloading CUDA libraries. `marker-pdf` pins an old `pillow` with no Python 3.14 wheel, so the project is pinned to Python 3.12.

## 5. Open Questions

- ~~Implementation language~~ **Decided: Python 3.12** (2026-11-02), chosen mainly for PDF/math extraction tooling; pinned to 3.12 because ML dependencies lack 3.14 wheels. Package manager **decided: `uv`**; test runner **`pytest`**; lint **`ruff`**. Still open: LLM provider/SDK (likely the Anthropic SDK if the vision fallback is used).
- Structured state format: sheet vs. CSV vs. JSON vs. SQLite. **Deferred to M3** (2026-11-02).
- Textbook extraction tooling and the math/equation strategy. **In progress:** try `marker-pdf` locally first, vision-model fallback (see M1 findings).
- Interface: CLI only, or a lightweight local web UI.
- How to evaluate teaching quality (even informally) so changes to the prompt and skills can be compared.
- Privacy boundaries: which learner files, if any, must be off-limits to the agent.
- Whether the learner's notes live inside the repo's data directory or in an external note system the harness points to.

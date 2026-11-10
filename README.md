# velocity

A local subject tutor built on Claude. You pick a subject; the tutor teaches, asks before it tells, quizzes you, and tracks what you know. It can ground its teaching in your own sources (a textbook PDF), citing them by section. Every session is written to a log you read and annotate in [Obsidian](https://obsidian.md), and your own notes stay yours: the tutor can read and critique them, but never write them.

It is built around one **MCP server** with two front ends:

- **`velocity tutor`** runs the tutor inside [Claude Code](https://claude.com/claude-code), using your Claude plan or API key.
- **`velocity chat`** is velocity's own agent loop on the Anthropic API, with the same tools, prompt and skills.

A local web UI, **`velocity web`**, puts both in your browser: chat with the tutor through Claude Code (on your plan) or the API (with its cost shown as you go), with math rendered, and browse, resume or continue past sessions. A paused session can be resumed on either engine, whichever one started it. A reader beside the chat shows your sources (the tutor's citations are links into it); you can add a source there, and prepare a book ahead of time so reading it is instant. Subjects are managed on the page too: create them, change their settings (name, extra instructions for the tutor, note folders), and archive, recover or delete them.

## What a session looks like

An excerpt from a real session log, as it appears in Obsidian (math renders there). The tutor had cited Stewart, *Calculus*, §2.3 earlier in the session. The unlabeled paragraph is an answer the learner typed straight into the log; the tutor picks up such annotations at its next turn.

> **Tutor:** Last time we left one problem unattempted, Stewart §2.3 #15:
>
> $$\lim_{t\to-3}\frac{t^2-9}{2t^2+7t+3}$$
>
> Try it now. As a first step, what happens if you plug in $t=-3$ directly, and what does that tell you about what to do next?
>
> The first step is that t = -3 causes the equation to resolve to 0. That means that we have to factor this equation and see what happens to the factors. I do not remember how to factor the bottom equation, so I would need a hint to get started.
>
> **You:** I inputted my answer in the session log.
>
> **Tutor:** Your plan is right: factor and see what happens to the factors. One correction to the wording. Plugging in $t=-3$ gives $\frac{0}{0}$, not just 0, because the numerator and the denominator both become 0. That $\frac00$ is the sign that a common factor is probably hiding.
>
> **Hint for the denominator:** $t=-3$ makes $2t^2+7t+3$ equal 0. So what factor must the denominator contain? Write that factor, then work out what the other factor has to be so the product is $2t^2+7t+3$.

## Features

- **Teaching, not answering.** The tutor diagnoses what you know, gives hints before answers, mixes earlier material back into new questions, and says so when you (or it) are wrong.
- **Optional sources.** Attach a textbook (PDF, EPUB, Markdown or HTML) and the tutor reads it section by section and cites it (`Stewart §2.4`). For PDFs: Math is OCR'd to LaTeX with [marker](https://github.com/datalab-to/marker) and cached locally. Text the OCR silently drops is detected against the PDF's text layer and restored. Without sources, it teaches from general knowledge and says so once per session.
- **Session logs.** The dialogue is logged word for word by hooks, not by the model, to an append-only record and a markdown log in your vault. Text you type into the log is kept as your annotation. The tutor gives each session a title naming what it covered, so you can find it later. An interrupted session can be resumed; a finished one can be continued in a new session that picks up where it left off.
- **Learner state.** Topics, every answered question, and open threads are stored per subject, with spaced review (a correct answer doubles a topic's review interval; a wrong one resets it). A summary loads at the start of every session, and a readable `progress.md` sits next to it.
- **Your notes stay yours.** The tutor can read the note folders you allow and critique them on request, but it can't write to them. This is enforced by Claude Code permission rules, a hook and a test, not only by the prompt. Notes marked `private: true` are never read.
- **Skills.** Teaching behaviors you can invoke by name, or that the tutor picks up when they fit:

| Skill | What it does |
|---|---|
| `/quiz [topic] [n]` | A short quiz, one question at a time, hints before answers, every result recorded |
| `/socratic` | Leads you to the answer by questions only |
| `/explain-differently` | Re-explains the last concept from a different angle |
| `/assign-problems [section] [n]` | Assigns exercises (from your source when there is one), then checks your work |
| `/review-notes [note]` | Critiques your notes without rewriting them |
| `/end-session` | Recap, progress update, review schedule, a prompt to take notes |

## Setup

Requirements: Linux or macOS (Windows is best effort) and [uv](https://docs.astral.sh/uv/). To chat on your Claude plan you need [Claude Code](https://claude.com/claude-code) 2.1.314 or later; for the API engine (`velocity chat`, or API chats on the web page), an Anthropic API key.

**[Obsidian](https://obsidian.md) (free) is strongly recommended.** velocity keeps its session logs and progress as markdown in a folder of your notes, and Obsidian is the best way to work with them. Without it velocity still works, with any folder: the web page shows your chats, logs (math included) and books. What you'd miss:

- **Annotating your logs.** Write your own thoughts between the lines of a session log; the tutor reads them next session and responds.
- **Your own notes.** The tutor can read (never change) the notes you keep, and critique them with `/review-notes`. Without a notes app there's nowhere convenient to write them.
- **Browsing everything as linked notes**, outside velocity: logs, `progress.md`, and your notes side by side.

```bash
git clone https://github.com/sova-nova/velocity-framework.git
cd velocity-framework
uv sync
```

`uv sync` also installs the OCR dependencies (marker and CPU-only PyTorch, a few GB). Without textbook sources you can skip them: run `uv sync --no-group ocr` and use `uv run --no-sync velocity ...` instead of `uv run velocity ...`.

The easiest start is the web page: run `uv run velocity web`. On the first run it asks for your notes folder (suggesting the Obsidian vaults it finds), shows what this computer can do (Claude Code, an API key, OCR), and from there you create subjects, add books and chat. The same settings are under **Settings** later.

For the terminal only, create `~/.config/velocity/config.toml` yourself (the web page writes the same file):

```toml
vault_path = "~/Documents/MyVault"   # velocity writes only inside <vault>/Tutor/

# Only for OCR without an NVIDIA GPU: llama.cpp's llama-server (or set LLAMA_CPP_BINARY)
# llama_cpp_binary = "~/.local/opt/llama.cpp/llama-server"

# Only for velocity chat (both optional)
[api]
model = "sonnet"     # opus (default), sonnet, haiku, or a full model ID
effort = "medium"    # low, medium (default), high, xhigh, max
```

Then create a subject, and optionally attach a source and note folders:

```bash
uv run velocity new "Calculus"
uv run velocity source add calculus ~/books/stewart.pdf --name Stewart   # or .epub, .md, .html
uv run velocity notes add calculus "Math/Calculus"
```

Each subject has a `<vault>/Tutor/<subject>/subject.toml` you can edit, for example to give the tutor extra instructions for that subject.

## Usage

### `velocity tutor <subject>`: in Claude Code

```bash
uv run velocity tutor calculus
uv run velocity tutor calculus --resume   # continue the last session
```

This opens Claude Code in a tutor workspace that velocity regenerates on every launch, so the tutor never runs in this code repo, and your own Claude Code settings and `CLAUDE.md` don't leak into it. It works with your Claude plan or an API key.

### `velocity chat <subject>`: velocity's own agent loop

```bash
export ANTHROPIC_API_KEY=...
uv run velocity chat calculus --model sonnet
```

The same tutor in your terminal, streamed from the Anthropic API. Type `/model` to see or switch the model mid-chat (`/model haiku`), and `/exit` or Ctrl+D to leave. On exit it prints the session's token use and estimated cost per model.

### Choosing a model

`velocity chat` is billed per token, unlike Claude Code on a flat subscription. The default is Opus 5.5.

- **Opus 5.5:** the best tutor, and the most expensive.
- **Sonnet 5.5:** about half the price of Opus and a good everyday choice.
- **Haiku 5.5:** very cheap, but in testing it did not reliably call the tools that record progress, so learner state can fall behind.

### Other commands

| Command | What it does |
|---|---|
| `velocity subjects` | List subjects |
| `velocity sessions <subject>` | List a subject's sessions with their titles |
| `velocity tutor <subject> --continue <session>` | Start a new session that picks up from a finished one (also for `velocity chat`) |
| `velocity web [subject]` | Open the local web UI at http://127.0.0.1:8765: chat (through Claude Code, or the API if `ANTHROPIC_API_KEY` is set) and session history |
| `velocity source list <subject>` | List a subject's sources |
| `velocity source remove <subject> <name>` / `velocity source rename <subject> <name> <new name>` | Take a source out of a subject (the file is kept, and stays in the web page's library), or change the name the tutor cites it by |
| `velocity source recheck <subject> [--all] [--source <name>]` | OCR prepared pages again with the dropped-text check (by default only pages prepared before it), with a progress bar. Also in the web reader (Prepare… → Already prepared) |
| `velocity source prepare <subject> --section ch14 \| --pages 920-959 \| --all` | OCR a chapter, a page range or the whole book ahead of time, with a progress bar, so reading it later is instant. It takes a while (about 6-7 s per page on a GPU; about 2.5 hours for a 1,400-page book). Also in the web reader (Prepare…), which runs it in the background |
| `velocity notes list <subject>` | List the notes the tutor may read |

### Where things live

| What | Where |
|---|---|
| Config | `~/.config/velocity/config.toml` |
| Subject settings, session logs, learner state | `<vault>/Tutor/<subject>/` |
| Session records (JSONL, the source of truth), OCR cache, generated workspaces | `~/.local/share/velocity/` |

The OCR cache stays outside the vault, so copyrighted text never syncs or gets indexed with your notes.

## Privacy

Everything runs locally, but what you say, the source sections the tutor reads, and any notes it reads are sent to Anthropic's API (directly in `velocity chat`, through Claude Code in `velocity tutor`). The tutor reminds you of this at the start of every session.

## How it works

```
   velocity tutor <subject>                velocity chat <subject>
   Claude Code in a generated              own streaming agent loop
   workspace (hooks, skills, settings)     (anthropic SDK)
               |                                    |
               +-----------------+------------------+
                                 |
                   velocity MCP server (stdio)
                                 |
               plain typed Python functions
     sources: list_sources, get_outline, search_source, view_page, read_section, get_exercises
     log:     append_log, list_sessions, read_session, check_log, end_session
     state:   read_state, record_question, set_topic_status, open/close_thread
     notes:   list_notes, read_note (read-only)
```

- **One core, two front ends.** The tools are plain typed functions, unit-tested without a server, behind a thin MCP layer. The tutor prompt and skills are plain text files in [`src/velocity/template/`](src/velocity/template/) and back both front ends.
- **Logging by hooks.** In Claude Code, hooks capture each turn verbatim. `velocity chat` calls the same hook functions directly, so logs, state and recaps behave the same in both.
- **The own loop** (`src/velocity/agent.py`) streams responses, runs MCP tools, loads skills on demand, uses prompt caching and refusal fallbacks, keeps the history append-only (a failed turn is rolled back), and tracks cost.

[`DESIGN.md`](DESIGN.md) has the full design: goals, decisions, milestones, and findings (such as an evaluation of math OCR on a real textbook).

## Development

```bash
uv run pytest                     # ~140 tests; real-OCR test: VELOCITY_OCR_TESTS=1
uv run ruff check . && uv run ruff format .
```

CI runs lint, format and tests on every pull request.

## Status

Milestones 1 to 6 are done: sources, the Claude Code tutor, learner state, skills, notes, and the own agent loop. Next: teaching-quality evals that replay recorded sessions, OCR repair for dropped text and non-PDF sources, then a web front end.

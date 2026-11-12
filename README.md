# velocity

A local subject tutor built on Claude. You pick a subject; the tutor teaches, asks before it tells, quizzes you, and tracks what you know. It can ground its teaching in your own sources (a textbook PDF), citing them by section. Every session is written to a log you read and annotate in [Obsidian](https://obsidian.md), and your own notes stay yours: the tutor can read and critique them, but never write them.

It is built around one **MCP server** with two front ends:

- **`velocity tutor`** runs the tutor inside [Claude Code](https://claude.com/claude-code), using your Claude plan or API key.
- **`velocity chat`** is velocity's own agent loop on the Anthropic API, with the same tools, prompt and skills.

A local web UI, **`velocity web`**, puts both in your browser: chat with the tutor through Claude Code (on your plan) or the API (with its cost shown as you go), or, for free, on a model on your own computer or a hosted free tier ([weaker; see below](#free-models)), with math rendered, and browse, resume or continue past sessions. A paused session can be resumed on either engine, whichever one started it. A reader beside the chat shows your sources (the tutor's citations are links into it); you can add a source there, and prepare a book ahead of time so reading it is instant. Subjects are managed on the page too: create them, change their settings (name, extra instructions for the tutor, note folders), and archive, recover or delete them.

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
- **Learner state.** Topics, every answered question, and open threads are stored per subject, with spaced review (a correct answer doubles a topic's review interval; a wrong one resets it). A summary loads at the start of every session, and a readable `progress.md` sits next to it. The web page's Progress tab shows the same: what's due for review, weak spots, open threads, and every topic with its questions.
- **Your notes stay yours.** The tutor can read the note folders you allow and critique them on request, but it can't write to them. This is enforced by Claude Code permission rules, a hook and a test, not only by the prompt. Notes marked `private: true` are never read.
- **Skills.** Teaching behaviors you can invoke by name, or that the tutor picks up when they fit:

| Skill | What it does |
|---|---|
| `/quiz [topic] [n]` | A short quiz, one question at a time, hints before answers, every result recorded |
| `/socratic` | Leads you to the answer by questions only |
| `/explain-differently` | Re-explains the last concept from a different angle |
| `/assign-problems [section] [n]` | Assigns exercises (from your source when there is one), then checks your work |
| `/review-notes [note]` | Critiques your notes without rewriting them |
| `/next` | Suggests two or three things to do next (open threads, weak spots, the next section) without starting any |
| `/end-session` | Recap, progress update, review schedule, a prompt to take notes |

## Install the app (Windows and Linux, no terminal)

Download from the [latest release](https://github.com/sova-nova/velocity-framework/releases/latest):

- **Windows:** `velocity-<version>-windows-setup.exe`. Run it; it installs for you only (no administrator rights) and adds velocity to the Start menu. velocity isn't signed, so Windows warns once about an unknown publisher: click **More info**, then **Run anyway**. Prefer not to install? The **portable** zip (`velocity-<version>-windows-portable.zip`) works too: unzip it anywhere and open `velocity.exe`.
- **Linux:** `velocity-<version>-x86_64.AppImage`. Make it executable (in your file manager: right-click → Properties → Permissions → "Is executable", or `chmod +x`) and open it. The first time, it offers to add velocity to your app menu. If it doesn't open, your system may need FUSE (`fuse` or `libfuse2` in your package manager).

The first time, velocity asks for your notes folder. Then pick how the tutor runs: your **Anthropic API key** (paste it in Settings; it's saved in your system's keyring or Credential Manager), a [free model](#free-models) (Settings → **Download model**, with [Ollama](https://ollama.com/download) installed), or Claude Code if you already use it (untested on Windows). **Settings → Check for updates** tells you when a new version is out (velocity never checks on its own). Uninstalling keeps your settings, sessions and keys.

The downloads don't include OCR: PDFs with selectable text, EPUB, Markdown and HTML work, and the tutor can look at pages, but scanned or math-heavy PDFs need the full install below. A macOS download is planned; on a Mac, use the install below for now.

To build them yourself: `packaging/build_appimage.sh` on Linux, `packaging\build_windows.ps1` on Windows (needs [Inno Setup](https://jrsoftware.org/isinfo.php) too). Both need `uv`, build in `build/`, and leave your environment alone.

## Setup

Requirements: Linux, Windows or macOS (the test suite runs on all three) and [uv](https://docs.astral.sh/uv/). To chat on your Claude plan you need [Claude Code](https://claude.com/claude-code) 2.1.314 or later; for the API engine (`velocity chat`, or API chats on the web page), an Anthropic API key. Without either, the web page can use a [free model](#free-models) (weaker).

**[Obsidian](https://obsidian.md) (free) is strongly recommended.** velocity keeps its session logs and progress as markdown in a folder of your notes, and Obsidian is the best way to work with them. Without it velocity still works, with any folder: the web page shows your chats, logs (math included), books and progress. What you'd miss:

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

To use velocity as an app in its own window instead of a browser tab, run `uv run velocity app` (Linux and Windows; macOS should work but is untested). On first run, setup offers to add velocity to your app menu, so after that you can open it without a terminal; **Settings → Add velocity to the app menu** (or `uv run velocity install-launcher`) does it later. Closing the window pauses any open chat. Started from the menu, the app doesn't see keys exported in a terminal, so add your API key in **Settings** (it's saved in your system keyring). For a ready-made download instead, see [Install the app](#install-the-app-windows-and-linux-no-terminal).

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
export ANTHROPIC_API_KEY=...    # or save the key once in the web page's Settings
uv run velocity chat calculus --model sonnet
uv run velocity chat calculus --resume    # resume the last paused session (or --resume <session>)
uv run velocity chat calculus --engine openai    # a free model instead (see Free models)
```

The same tutor in your terminal, streamed from the Anthropic API. `--resume` picks up a paused session from any front end on the engine and model it last ran on, and shows the last three messages so you know where you were. Type `/model` to see or switch the model mid-chat (`/model haiku`), and `/exit` or Ctrl+D to leave. On exit it prints the session's token use and estimated cost per model.

With `--engine openai`, the chat runs on the [free model](#free-models) set up in Settings (or `--model qwen3:8b` for another one on the same server). It needs no API key and shows no cost.

### Choosing a model

`velocity chat` is billed per token, unlike Claude Code on a flat subscription. The default is Opus 5.5.

- **Opus 5.5:** the best tutor, and the most expensive.
- **Sonnet 5.5:** about half the price of Opus and a good everyday choice.
- **Haiku 5.5:** very cheap, but in testing it did not reliably call the tools that record progress, so learner state can fall behind.

### Free models

On the web page (and in the terminal with `velocity chat <subject> --engine openai`), the **Free model (weaker)** engine runs the same tutor on any server that speaks the OpenAI chat API: a model on your own computer, or a hosted free tier. It costs nothing, but it is **noticeably weaker** than Claude. Small models follow the tutor's instructions less reliably, often forget to record your progress, and can grade a wrong answer as right, so double-check anything important. Set it up under **Settings → Free / other model** (pick a preset, then press **Test**).

**On your computer, with [Ollama](https://ollama.com/download).** Free and private: nothing leaves your computer. Install and start Ollama (on Linux it may also be in your distribution's app store), then in **Settings → Free / other model** press **Download model**. velocity downloads `qwen3:4b` (about 2.5 GB) with progress, makes its own copy of it with room for the tutor's instructions built in, and sets it up; no terminal needed.

The room matters: Ollama's default context (4,096 tokens) is smaller than the tutor's instructions alone (about 5,000), and Ollama cuts what doesn't fit without saying so. velocity's copy (`velocity-qwen3-4b`) has a 32K context built in. `qwen3:4b` needs about 8 GB of GPU memory with it. To use another Ollama model yourself, start Ollama with a larger context and set the same number in Settings (the Test button checks it):

```bash
ollama pull qwen3:8b
OLLAMA_CONTEXT_LENGTH=32768 ollama serve
# If Ollama runs as a system service instead:
#   sudo systemctl edit ollama   ->  [Service] Environment="OLLAMA_CONTEXT_LENGTH=32768"
```

**A hosted free tier, e.g. [OpenRouter](https://openrouter.ai/models?max_price=0).** Make an account and a key, pick the OpenRouter preset, paste the key into **Save key** (or set `OPENROUTER_API_KEY` in the terminal that starts velocity), and enter a model id ending in `:free`. Free tiers have rate limits, their models and terms change often, and they may use your conversations for training.

**How weak?** Measured with the eval cases (one tutor turn each, replayed from real sessions; code checks only), on an AMD RX 7700 XT (12 GB):

| Model | Cases passing every check | Records the learner's answer when it should | Calls any tool | Time per reply |
|---|---|---|---|---|
| `qwen3:4b` | 23 / 38 | 3 / 7 | 18 / 38 cases | about 50 seconds |
| `qwen3:8b` | 18 / 38 | 0 / 7 | 7 / 38 cases | about 30 seconds |

The bigger model did worse: it was faster, but mostly because it rarely used tools at all. `qwen3:4b` is the preset's suggestion. Both graded some imprecise answers as fully correct. For comparison, Claude (Opus and Sonnet) passed every check on the 3 cases it has been run on so far (the full Claude run is still to come).

### Other commands

| Command | What it does |
|---|---|
| `velocity subjects` | List subjects |
| `velocity sessions <subject>` | List a subject's sessions with their titles |
| `velocity sessions <subject> --search "<words>"` | Sessions with all these words in their title or log, with a snippet of each match. Also in the History tab's search box |
| `velocity sessions [<subject>] --remove-empty` | Find sessions with nothing in them (or only a wrap-up of nothing), in one subject or all, and remove them after you confirm. Empty sessions are removed when they end; this cleans up older ones. Also in Settings (Find empty sessions…) |
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

**Your keys.** An API key you add in Settings is saved in your system keyring (KWallet or GNOME Keyring on Linux, the Keychain on macOS, the Credential Manager on Windows), never in velocity's files, and the web page never gets it back. An environment variable (`ANTHROPIC_API_KEY`, or the one named for a free model's server) wins over a saved key. A free model's key is saved for its server and only ever sent there.

**What you share.** Everything runs locally, but what you say, the source sections the tutor reads, and any notes it reads are sent to Anthropic's API (directly in `velocity chat`, through Claude Code in `velocity tutor`). The tutor reminds you of this at the start of every session. With a [free model](#free-models) on your own computer, nothing leaves it; with a hosted free tier, it all goes to that service instead, and free tiers may use conversations for training. The web page says which applies before you start.

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

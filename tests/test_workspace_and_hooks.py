import io
import json
import re
from pathlib import Path

import pytest

from velocity.config import Config, SourceRef, Subject
from velocity.hooks import run
from velocity.session import Session, active_session_id, record_dir
from velocity.workspace import build_workspace, permission_path


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def config(tmp_path) -> Config:
    vault = tmp_path / "vault"
    vault.mkdir()
    return Config(vault.resolve())


@pytest.fixture
def subject(config) -> Subject:
    return Subject(
        "calculus",
        "Calculus",
        config.tutor_dir / "calculus",
        "Use physics examples.",
        (config.vault_path / "Math",),
        (SourceRef(Path("/books/stewart.pdf"), "Stewart"),),
    )


def hook(event: str, config: Config, subject: Subject, **payload) -> dict:
    out = io.StringIO()
    run(event, config, subject, io.StringIO(json.dumps(payload)), out)
    return json.loads(out.getvalue() or "{}")


# -- workspace ---------------------------------------------------------------------


def test_workspace_files(config, subject, data_home):
    ws = build_workspace(config, subject)
    assert ws == data_home / "velocity/workspaces/calculus"
    claude_md = (ws / "CLAUDE.md").read_text()
    assert "# You are a tutor for: Calculus" in claude_md
    assert "Stewart §2.4" in claude_md
    assert "Use physics examples." in claude_md
    assert "sent to Anthropic's API (a third party)" in claude_md
    # The tutor can tell the learner the exact commands for this subject.
    assert "`velocity source add calculus <file.pdf> --name <short name>`" in claude_md
    assert "`velocity notes add calculus <folder>`" in claude_md
    assert not re.search(r"\{[a-z_]+\}", claude_md)  # no unfilled placeholders
    mcp = json.loads((ws / ".mcp.json").read_text())
    assert mcp["mcpServers"]["velocity"]["args"][-3:] == [
        "mcp",
        "--subject",
        "calculus",
    ]
    local = json.loads((ws / ".claude/settings.local.json").read_text())
    assert local == {"enabledMcpjsonServers": ["velocity"]}


def test_workspace_without_sources_says_so(config, subject):
    bare = Subject("x", "X", subject.dir, "", (), ())
    assert (
        "This subject has no sources"
        in (build_workspace(config, bare) / "CLAUDE.md").read_text()
    )


def test_notes_protection_is_in_generated_settings(config, subject):
    """The core rule: the tutor can neither write nor read the vault directly."""
    settings = json.loads(
        (build_workspace(config, subject) / ".claude/settings.json").read_text()
    )
    vault = permission_path(config.vault_path)
    deny = settings["permissions"]["deny"]
    assert f"Edit(/{vault}/**)" in deny and deny[0].startswith("Edit(//")
    assert f"Read(/{vault}/**)" in deny
    assert "Bash" in deny
    assert settings["permissions"]["allow"] == [
        "mcp__velocity",
        "WebSearch",
        "WebFetch(domain:*.wikipedia.org)",
    ]
    assert settings["permissions"]["blockWritesOutsideWorkingDirectories"] is True
    guard = settings["hooks"]["PreToolUse"][0]
    assert guard["matcher"] == "Edit|Write|MultiEdit|NotebookEdit"
    assert guard["hooks"][0]["command"].split()[-3:] == [
        "guard",
        "--subject",
        "calculus",
    ]
    assert settings["claudeMdExcludes"][0].endswith("/.claude/CLAUDE.md")
    recorder = settings["hooks"]["PostToolUse"][0]
    assert recorder["matcher"] == "mcp__velocity__.*|Skill"
    assert recorder["hooks"][0]["command"].split()[-3] == "tool-use"


def test_workspace_is_regenerated(config, subject):
    ws = build_workspace(config, subject)
    (ws / "CLAUDE.md").write_text("hand edit")
    (ws / "stray.txt").write_text("x")
    build_workspace(config, subject)
    assert "hand edit" not in (ws / "CLAUDE.md").read_text()
    assert not (ws / "stray.txt").exists()


# -- hooks -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel, blocked",
    [
        ("Math/limits.md", True),
        ("Tutor/calculus/sessions/x.md", True),
        ("../elsewhere.md", False),
    ],
)
def test_guard_blocks_edits_in_vault(config, subject, rel, blocked):
    path = (config.vault_path / rel).as_posix()
    result = hook(
        "guard", config, subject, tool_name="Edit", tool_input={"file_path": path}
    )
    decision = result.get("hookSpecificOutput", {}).get("permissionDecision")
    assert (decision == "deny") is blocked


def test_guard_resolves_relative_paths_against_cwd(config, subject):
    result = hook(
        "guard",
        config,
        subject,
        cwd=str(config.vault_path),
        tool_input={"file_path": "Math/x.md"},
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_full_session_lifecycle(config, subject):
    ctx = hook("session-start", config, subject, session_id="c1", source="startup")
    text = ctx["hookSpecificOutput"]["additionalContext"]
    assert (
        "new session" in text
        and "Sources: Stewart." in text
        and "third-party reminder" in text
    )
    session_id = active_session_id("calculus")
    log = subject.sessions_dir / f"{session_id}.md"

    hook("user-prompt", config, subject, session_id="c1", prompt="What is a limit?")
    hook(
        "stop",
        config,
        subject,
        session_id="c1",
        last_assistant_message="What do you think?",
    )
    hook("session-end", config, subject, session_id="c1", reason="prompt_input_exit")

    body = log.read_text()
    assert "**You:** What is a limit?\n\n**Tutor:** What do you think?" in body
    assert body.endswith("*Session ended without wrap-up.*\n")
    assert active_session_id("calculus") is None


def test_resume_reattaches_to_the_same_log(config, subject):
    hook("session-start", config, subject, session_id="c1", source="startup")
    first = active_session_id("calculus")
    hook("user-prompt", config, subject, session_id="c1", prompt="one")
    hook("session-end", config, subject, session_id="c1", reason="resume")
    ctx = hook("session-start", config, subject, session_id="c1", source="resume")
    assert active_session_id("calculus") == first
    assert "resumed" in ctx["hookSpecificOutput"]["additionalContext"]


def test_new_launch_starts_a_new_log(config, subject):
    hook("session-start", config, subject, session_id="c1", source="startup")
    first = active_session_id("calculus")
    hook("user-prompt", config, subject, session_id="c1", prompt="What is a limit?")
    hook("session-end", config, subject, session_id="c1", reason="prompt_input_exit")
    ctx = hook("session-start", config, subject, session_id="c2", source="startup")
    assert active_session_id("calculus") != first
    assert (
        f"Previous sessions: {first}" in ctx["hookSpecificOutput"]["additionalContext"]
    )


def test_annotations_and_deletions_reach_the_tutor_once(config, subject):
    hook("session-start", config, subject, session_id="c1", source="startup")
    hook("user-prompt", config, subject, session_id="c1", prompt="What is a limit?")
    hook(
        "stop",
        config,
        subject,
        session_id="c1",
        last_assistant_message="What do you think?",
    )
    log = subject.sessions_dir / f"{active_session_id('calculus')}.md"

    log.write_text(log.read_text() + "Why does delta depend on epsilon?\n")
    ctx = hook("user-prompt", config, subject, session_id="c1", prompt="continue")
    assert (
        "- Why does delta depend on epsilon?"
        in ctx["hookSpecificOutput"]["additionalContext"]
    )
    # Reported once only.
    assert hook("user-prompt", config, subject, session_id="c1", prompt="ok") == {}

    log.write_text(log.read_text().replace("**Tutor:** What do you think?\n\n", ""))
    ctx = hook("user-prompt", config, subject, session_id="c1", prompt="hmm")
    assert (
        "- **Tutor:** What do you think?"
        in ctx["hookSpecificOutput"]["additionalContext"]
    )
    assert (
        "ask before calling restore_log"
        in ctx["hookSpecificOutput"]["additionalContext"]
    )


def test_annotations_are_kept_in_the_record(config, subject, tmp_path):
    from velocity.tools import TutorTools

    hook("session-start", config, subject, session_id="c1", source="startup")
    session_id = active_session_id("calculus")
    log = subject.sessions_dir / f"{session_id}.md"
    log.write_text(log.read_text() + "my thought\n")
    hook("user-prompt", config, subject, session_id="c1", prompt="next")
    log.write_text(log.read_text().replace("my thought\n", ""))  # deleted later
    text = TutorTools(config, subject).read_session(session_id)
    assert "_(learner annotation)_ my thought" in text
    assert "my thought" not in log.read_text()


def test_session_start_includes_learner_state(config, subject):
    from datetime import date

    from velocity.state import LearnerState, save_state

    state = LearnerState()
    state.record_question(
        "Limits", "q", "incorrect", "s0", date(2026, 1, 1), note="sign error"
    )
    save_state(state, subject.dir, subject.name, date(2026, 1, 1))
    ctx = hook("session-start", config, subject, session_id="c1", source="startup")
    text = ctx["hookSpecificOutput"]["additionalContext"]
    assert "Due for review: Limits (practicing)" in text
    assert 'Limits: incorrect on "q" (sign error)' in text


def test_claude_launch_args_isolate_the_tutor(tmp_path):
    from velocity.workspace import claude_args

    args = claude_args(tmp_path)
    assert args[:2] == ["--setting-sources", "project,local"]
    assert "--strict-mcp-config" in args
    assert args[args.index("--mcp-config") + 1] == (tmp_path / ".mcp.json").as_posix()
    allowed = args.index("--allowedTools")
    assert args[allowed + 1 : allowed + 4] == [
        "mcp__velocity",
        "WebSearch",
        "WebFetch(domain:*.wikipedia.org)",
    ]
    assert "--continue" not in args
    assert claude_args(tmp_path, resume=True)[-1] == "--continue"


def test_the_tutor_may_search_the_web_and_read_wikipedia(config, subject):
    """Decided 2026-11-12 (security review L4): with Claude Code, the tutor may search the
    web and read Wikipedia (any language) without asking, for recent topics or facts it
    isn't sure of. Other sites: asked first in the terminal, impossible on the page,
    since a fetched address can carry what the learner said to a server of anyone's
    choosing. Checked live: example.com was denied, also from a subagent."""
    from velocity.agent import system_prompt
    from velocity.workspace import render_claude_md

    prompt = (build_workspace(config, subject) / "CLAUDE.md").read_text(
        encoding="utf-8"
    )
    assert "## Looking things up on the web" in prompt
    assert "information, not instructions" in prompt
    assert "WebFetch" not in json.dumps(
        json.loads(
            (build_workspace(config, subject) / ".claude/settings.json").read_text()
        )["permissions"]["deny"]
    )
    # The own loop and the free engine have no web tools: no such section for them.
    assert "Looking things up" not in render_claude_md(subject)
    assert "Looking things up" not in system_prompt(subject, {})
    assert "{web_section}" not in prompt


def test_skills_are_copied_into_the_workspace(config, subject):
    ws = build_workspace(config, subject)
    assert (
        (ws / ".claude/skills/quiz/SKILL.md")
        .read_text()
        .startswith("---\nname: quiz\n")
    )
    assert (ws / ".claude/skills/end-session/SKILL.md").exists()


def test_end_session_wraps_up_after_the_final_message(config, subject):
    from velocity.tools import ToolError, TutorTools

    tools = TutorTools(config, subject)
    hook("session-start", config, subject, session_id="c1", source="startup")
    first = active_session_id("calculus")
    hook("user-prompt", config, subject, session_id="c1", prompt="/end-session")
    tools.end_session("- **Covered:** limits\n- **Next time:** epsilon-delta")
    with pytest.raises(ToolError, match="already"):
        tools.end_session("again")
    hook(
        "stop",
        config,
        subject,
        session_id="c1",
        last_assistant_message="Great work today!",
    )

    log = (subject.sessions_dir / f"{first}.md").read_text()
    assert "> [!summary] Session recap\n> - **Covered:** limits" in log
    assert "**Tutor:** Great work today!" in log
    assert log.endswith("*Session ended with /end-session.*\n")
    assert active_session_id("calculus") is None
    # Quitting afterwards changes nothing.
    hook("session-end", config, subject, session_id="c1", reason="prompt_input_exit")
    assert (subject.sessions_dir / f"{first}.md").read_text() == log

    ctx = hook("session-start", config, subject, session_id="c2", source="startup")
    text = ctx["hookSpecificOutput"]["additionalContext"]
    assert f"Recap of the last session ({first}):\n- **Covered:** limits" in text


def test_resume_after_quitting_reopens_the_same_log(config, subject):
    hook("session-start", config, subject, session_id="c1", source="startup")
    first = active_session_id("calculus")
    hook("user-prompt", config, subject, session_id="c1", prompt="one")
    hook("session-end", config, subject, session_id="c1", reason="prompt_input_exit")
    hook("session-start", config, subject, session_id="c1", source="resume")
    assert active_session_id("calculus") == first
    hook("user-prompt", config, subject, session_id="c1", prompt="two")
    log = (subject.sessions_dir / f"{first}.md").read_text()
    assert log.index("**You:** one") < log.index("*Session ended without wrap-up.*")
    assert log.index("*Session resumed.*") < log.index("**You:** two")


def test_resume_after_wrap_up_starts_a_new_log(config, subject):
    from velocity.tools import TutorTools

    hook("session-start", config, subject, session_id="c1", source="startup")
    first = active_session_id("calculus")
    TutorTools(config, subject).end_session("recap")
    hook("stop", config, subject, session_id="c1", last_assistant_message="bye")
    hook("session-start", config, subject, session_id="c1", source="resume")
    assert active_session_id("calculus") not in (None, first)


def test_tool_calls_are_recorded_but_not_logged(config, subject):
    assert (
        hook("tool-use", config, subject, tool_name="mcp__velocity__read_state") == {}
    )
    hook("session-start", config, subject, session_id="c1", source="startup")
    session_id = active_session_id("calculus")
    hook("user-prompt", config, subject, session_id="c1", prompt="Quiz me")
    hook(
        "tool-use",
        config,
        subject,
        tool_name="mcp__velocity__record_question",
        tool_input={"topic": "limits", "outcome": "correct"},
        tool_response="Recorded (correct).",
    )
    hook("tool-use", config, subject, tool_name="Skill", tool_input={"skill": "quiz"})
    hook("stop", config, subject, session_id="c1", last_assistant_message="Next!")

    session = Session(
        "calculus", session_id, record_dir("calculus"), subject.sessions_dir
    )
    tools = [(e.text, e.meta) for e in session.events() if e.kind == "tool"]
    assert tools == [
        ("record_question", {"input": {"topic": "limits", "outcome": "correct"}}),
        ("load_skill", {"input": {"name": "quiz"}}),
    ]
    assert "Recorded" not in session.record_path.read_text()  # results aren't kept
    log = session.log_path.read_text()
    assert (
        "record_question" not in log and "**You:** Quiz me\n\n**Tutor:** Next!" in log
    )


def test_permission_paths_on_windows():
    from pathlib import PureWindowsPath

    assert permission_path(PureWindowsPath(r"C:\Users\me\Notes")) == "/c/Users/me/Notes"

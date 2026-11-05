import re
from importlib.resources import files

import pytest

SKILLS_DIR = files("velocity").joinpath("template/skills")
SKILLS = sorted(p.name for p in SKILLS_DIR.iterdir() if p.is_dir())


def frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "SKILL.md must start with a --- frontmatter block"
    return dict(line.split(": ", 1) for line in m[1].splitlines() if ": " in line)


def test_all_planned_skills_exist():
    assert {
        "quiz",
        "end-session",
        "socratic",
        "explain-differently",
        "assign-problems",
        "review-notes",
    } <= set(SKILLS)


@pytest.mark.parametrize("skill", SKILLS)
def test_skill_frontmatter(skill):
    meta = frontmatter(
        SKILLS_DIR.joinpath(skill, "SKILL.md").read_text(encoding="utf-8")
    )
    assert meta["name"] == skill
    assert 40 < len(meta["description"]) < 1024


@pytest.mark.parametrize("skill", SKILLS)
def test_skills_only_reference_existing_tools(skill):
    from velocity.mcp_server import TOOL_NAMES

    body = SKILLS_DIR.joinpath(skill, "SKILL.md").read_text(encoding="utf-8")
    called = {w for w in re.findall(r"`([a-z_]+)`", body) if "_" in w}
    assert called <= set(TOOL_NAMES), called - set(TOOL_NAMES)

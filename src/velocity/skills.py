"""Skills: SKILL.md files with a name/description header and instructions.

The same files back both front ends. Claude Code loads them from the workspace; the own agent
loop (agent.py) lists names and descriptions in its system prompt and loads a skill's full text
when the learner types its slash command or the model calls load_skill.
"""

import re
from dataclasses import dataclass
from importlib.resources import files

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str


def parse_skill(text: str) -> Skill:
    m = _FRONT_MATTER.match(text)
    if not m:
        raise ValueError("SKILL.md must start with a --- front matter block")
    meta = {}
    for line in m[1].splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            meta[key.strip()] = value.strip().strip('"')
    return Skill(meta["name"], meta["description"], text[m.end() :].strip())


def load_skills() -> dict[str, Skill]:
    root = files("velocity").joinpath("template/skills")
    skills = {}
    for folder in sorted(root.iterdir(), key=lambda p: p.name):
        path = folder.joinpath("SKILL.md")
        if folder.is_dir() and path.is_file():
            skill = parse_skill(path.read_text(encoding="utf-8"))
            skills[skill.name] = skill
    return skills


def parse_slash_command(
    text: str, skills: dict[str, Skill]
) -> tuple[Skill, str] | None:
    """('/quiz limits 3') -> (quiz skill, 'limits 3'); None if not a known skill command."""
    m = re.match(r"^/([a-z][a-z0-9-]*)(?:\s+(.*))?$", text.strip(), re.DOTALL)
    if not m or m[1] not in skills:
        return None
    return skills[m[1]], (m[2] or "").strip()

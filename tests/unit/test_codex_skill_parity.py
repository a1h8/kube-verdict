"""Guard: the Codex plugin's bundled skill must stay identical to the root SKILL.md.

The Codex plugin (`.codex-plugin/`) ships its own copy of the skill under
`.codex-plugin/skills/kube-verdict/SKILL.md` because Codex resolves plugin skills
from the directory named in `plugin.json` (`skills: "./.codex-plugin/skills/"`),
while the root `SKILL.md` is what skills.sh and the Claude Code plugin load. This
test fails if the two drift, so the skill definition can never silently diverge
between distribution channels.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_codex_skill_matches_root():
    root_skill = (ROOT / "SKILL.md").read_text()
    codex_skill = (ROOT / ".codex-plugin" / "skills" / "kube-verdict" / "SKILL.md").read_text()
    assert root_skill == codex_skill, (
        "SKILL.md and .codex-plugin/skills/kube-verdict/SKILL.md have drifted; "
        "re-sync with: cp SKILL.md .codex-plugin/skills/kube-verdict/SKILL.md"
    )

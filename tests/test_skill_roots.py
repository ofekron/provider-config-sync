"""Claude reads ~/.agents/skills too (via BC runtime injection), so the sync
tool's Claude global skill roots must include it — not only ~/.claude/skills."""
import sys
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "packages" / "provider-config-sync-backend" / "src"),
)

from provider_config_sync_backend.api import (  # noqa: E402
    _agents_skills_dir,
    _skill_roots_for_provider,
)


def _roots(provider, scope):
    return [p.resolve() for _, p in _skill_roots_for_provider(provider, scope)]


def test_claude_global_skill_roots_include_agents_skills():
    provider = {"kind": "claude", "config_dir": "~/.claude"}
    roots = _roots(provider, "global")

    assert _agents_skills_dir().resolve() in roots
    assert (Path.home() / ".claude" / "skills").resolve() in roots


def test_claude_global_skill_roots_parity_with_gemini():
    # Claude now mirrors Gemini: shared ~/.agents/skills first, then native.
    claude = _roots({"kind": "claude", "config_dir": "~/.claude"}, "global")
    gemini = _roots({"kind": "gemini", "config_dir": "~/.gemini"}, "global")
    assert claude[0] == _agents_skills_dir().resolve()
    assert gemini[0] == _agents_skills_dir().resolve()

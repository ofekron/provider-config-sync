from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

import yaml

_CLAUDE_COMMAND = {
    "description": "Sync Claude, Codex, and Gemini provider config capabilities",
    "allowed-tools": "mcp__provider_config_sync__list_provider_config_capabilities, mcp__provider_config_sync__read_provider_config_entry, mcp__provider_config_sync__write_provider_config_entry, mcp__provider_config_sync__apply_provider_config_entry, mcp__provider_config_sync__upsert_unified_capability_item, mcp__provider_config_sync__remove_unified_capability_item",
}

_SYNC_PROMPT = """Use Provider Config Sync for this provider capability change.

Workflow:
1. List provider config capabilities for the current project.
2. Find or create the matching unified capability.
3. Apply the unified capability to every configured provider that has an equivalent native config.
4. If a provider-specific config already has the better version, pull it into the unified capability first, then apply it outward.
5. Preserve provider-specific extensions instead of flattening them away.

Never edit only one provider-native config when the capability has equivalents in Claude, Codex, or Gemini.
"""


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path))).resolve()


def _write_new(path: Path, content: str, force: bool) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return f"exists: {path}"
    if path.exists():
        st = path.stat(follow_symlinks=False)
        if path.is_symlink() or not stat.S_ISREG(st.st_mode):
            return f"unsafe: {path}"
        path.write_text(content, encoding="utf-8")
        return f"wrote: {path}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return f"wrote: {path}"


def claude_command() -> str:
    frontmatter = yaml.safe_dump(_CLAUDE_COMMAND, sort_keys=False).strip()
    return f"---\n{frontmatter}\n---\n{_SYNC_PROMPT}"


def codex_skill() -> str:
    frontmatter = yaml.safe_dump(
        {
            "name": "provider-config-sync",
            "description": "Sync Claude, Codex, and Gemini provider config capabilities",
        },
        sort_keys=False,
    ).strip()
    return f"---\n{frontmatter}\n---\n{_SYNC_PROMPT}"


def gemini_command() -> str:
    prompt = _SYNC_PROMPT.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'description = "Sync Claude, Codex, and Gemini provider config capabilities"\nprompt = """{prompt}"""\n'


def install_agent_integrations(*, force: bool = False) -> list[str]:
    return [
        _write_new(_expand("~/.claude/commands/provider-config-sync.md"), claude_command(), force),
        _write_new(_expand("~/.agents/skills/provider-config-sync/SKILL.md"), codex_skill(), force),
        _write_new(_expand("~/.gemini/commands/provider-config-sync.toml"), gemini_command(), force),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Provider Config Sync native agent commands.")
    parser.add_argument("--force", action="store_true", help="overwrite existing integration command files")
    args = parser.parse_args()
    for line in install_agent_integrations(force=args.force):
        print(line)


if __name__ == "__main__":
    main()

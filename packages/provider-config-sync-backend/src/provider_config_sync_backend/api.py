"""REST endpoints for editing and syncing provider-native config files."""

from __future__ import annotations

import hashlib
import difflib
import json
import logging
import math
import os
import re
import stat
import tempfile
import threading
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/provider-config-sync", tags=["provider-config-sync"])

_KNOWN_KINDS = {"claude", "gemini", "codex"}
_DEFAULT_CONFIG_DIR = {
    "claude": "~/.claude",
    "gemini": "~/.gemini",
    "codex": "~/.codex",
}
_BACKUP_SUFFIX = ".bc-sync-backup"
_BACKUP_MARKER_SUFFIX = ".sha256"
_MCP_CAPABILITY_ID = "mcp"
_MCP_CAPABILITY_NAME = "MCP servers"
_INSTRUCTIONS_CAPABILITY_ID = "instructions"
_INSTRUCTIONS_CAPABILITY_NAME = "General instructions"
_MEMORY_CAPABILITY_ID = "memory"
_MEMORY_CAPABILITY_NAME = "Memory"
_SKILL_CAPABILITY_PREFIX = "skill-"
_AGENT_CAPABILITY_PREFIX = "agent-"
_COMMAND_CAPABILITY_PREFIX = "command-"
_CONTENT_FILE = "file"
_CONTENT_JSON_MCP = "json_mcp"
_CONTENT_TOML_MCP = "toml_mcp"
_CONTENT_MARKDOWN_SKILL = "markdown_skill"
_CONTENT_MARKDOWN_AGENT = "markdown_agent"
_CONTENT_TOML_AGENT = "toml_agent"
_CONTENT_MARKDOWN_COMMAND = "markdown_command"
_CONTENT_TOML_COMMAND = "toml_command"
_CONTENT_CODEX_COMMAND_SKILL = "codex_command_skill"
_CODEX_COMMAND_SKILL_PREFIX = "command-"
_CODEX_COMMAND_SKILL_KIND_KEY = "provider-config-sync-kind"
_CODEX_COMMAND_SKILL_NAME_KEY = "provider-config-sync-name"
_CODEX_COMMAND_SKILL_DESCRIPTION_KEY = "provider-config-sync-description"
_lock = threading.Lock()


def _default_sync_home() -> Path:
    raw = os.environ.get("PROVIDER_CONFIG_SYNC_HOME") or "~/.provider-config-sync"
    return _expand_path(raw)


def _default_encode_cwd(cwd: str) -> str:
    return hashlib.sha256(cwd.encode("utf-8")).hexdigest()


async def _noop_broadcast_changed(
    _scope: str,
    _category: str,
    _capability_id: str,
    _path: str,
    _cwd: str,
) -> None:
    return None


_provider_records_source: Callable[[], list[dict]] = lambda: []
_project_records_source: Callable[[], list[dict]] = lambda: []
_sync_home_source: Callable[[], Path] = _default_sync_home
_encode_cwd_source: Callable[[str], str] = _default_encode_cwd
_broadcast_changed_source: Callable[[str, str, str, str, str], Any] = _noop_broadcast_changed
_llm_review_source: Callable[[dict[str, Any]], list[str]] | None = None


def configure(
    *,
    provider_records: Callable[[], list[dict]] | None = None,
    project_records: Callable[[], list[dict]] | None = None,
    sync_home: Callable[[], Path] | None = None,
    encode_project_cwd: Callable[[str], str] | None = None,
    broadcast_changed: Callable[[str, str, str, str, str], Any] | None = None,
    llm_review: Callable[[dict[str, Any]], list[str]] | None = None,
) -> None:
    global _provider_records_source
    global _project_records_source
    global _sync_home_source
    global _encode_cwd_source
    global _broadcast_changed_source
    global _llm_review_source
    if provider_records is not None:
        _provider_records_source = provider_records
    if project_records is not None:
        _project_records_source = project_records
    if sync_home is not None:
        _sync_home_source = sync_home
    if encode_project_cwd is not None:
        _encode_cwd_source = encode_project_cwd
    if broadcast_changed is not None:
        _broadcast_changed_source = broadcast_changed
    if llm_review is not None:
        _llm_review_source = llm_review


@dataclass(frozen=True)
class Candidate:
    path: Path
    scope: str
    category: str
    capability_id: str
    capability_name: str
    provider_kind: str
    provider_name: str
    label: str
    language: str
    can_create: bool
    content_kind: str = "file"


@dataclass(frozen=True)
class ContentAdapter:
    read_current: Callable[[Path], tuple[str, bool]]
    write_if_unchanged: Callable[[Path, str | None, str, str], None]


def _expand_path(raw: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(raw)))).absolute()


def _local_project_root(cwd: str) -> Path:
    if not cwd:
        raise HTTPException(status_code=400, detail="project scope requires cwd")
    resolved = _expand_path(cwd).resolve()
    roots: list[Path] = []
    for project in _project_records_source():
        if (project.get("node_id") or "primary") != "primary":
            continue
        raw = project.get("path")
        if not raw:
            continue
        root = _expand_path(raw).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        roots.append(root)
    if not roots:
        raise HTTPException(status_code=400, detail="cwd is not inside a known local project")
    return max(roots, key=lambda root: len(root.parts))


def _dirs_from_root(root: Path, cwd: str) -> list[Path]:
    current = _expand_path(cwd).resolve()
    relative = current.relative_to(root)
    dirs = [root]
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        dirs.append(cursor)
    return dirs


def _read_json_dict(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_basenames(values: object) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    return [
        value
        for value in values
        if isinstance(value, str)
        and value not in {"", ".", ".."}
        and Path(value).name == value
    ]


def _gemini_context_names(
    config_dir: Path | None = None,
    project_root: Path | None = None,
) -> list[str]:
    settings_paths = [(config_dir or _expand_path(_DEFAULT_CONFIG_DIR["gemini"])) / "settings.json"]
    if project_root is not None:
        settings_paths.append(project_root / ".gemini" / "settings.json")
    for settings_path in reversed(settings_paths):
        context = _read_json_dict(settings_path).get("context")
        configured = context.get("fileName") if isinstance(context, dict) else None
        names = _safe_basenames(configured)
        if names:
            return names
    return ["GEMINI.md"]


def _codex_home(provider: dict | None = None) -> Path:
    if provider is not None and provider.get("config_dir"):
        return _provider_config_dir(provider)
    return _expand_path(os.environ.get("CODEX_HOME") or _DEFAULT_CONFIG_DIR["codex"])


def _codex_fallback_names(provider: dict | None = None) -> list[str]:
    try:
        config = tomllib.loads((_codex_home(provider) / "config.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    return _safe_basenames(config.get("project_doc_fallback_filenames"))


def _provider_records() -> list[dict]:
    return [
        provider
        for provider in _provider_records_source()
        if provider.get("kind") in _KNOWN_KINDS
    ]


def _provider_config_dir(provider: dict) -> Path:
    kind = provider.get("kind", "")
    raw = provider.get("config_dir") or _DEFAULT_CONFIG_DIR.get(kind, "")
    return _expand_path(raw).resolve()


def _global_instruction_candidates(provider: dict) -> list[Candidate]:
    kind = provider["kind"]
    name = provider.get("name") or kind
    if kind == "claude":
        return [
            Candidate(
                _provider_config_dir(provider) / "CLAUDE.md",
                "global",
                "instructions",
                _INSTRUCTIONS_CAPABILITY_ID,
                _INSTRUCTIONS_CAPABILITY_NAME,
                kind,
                name,
                "Claude instructions",
                "markdown",
                True,
            )
        ]
    if kind == "gemini":
        return [
            Candidate(
                _provider_config_dir(provider) / filename,
                "global",
                "instructions",
                _INSTRUCTIONS_CAPABILITY_ID,
                _INSTRUCTIONS_CAPABILITY_NAME,
                kind,
                name,
                f"Gemini instructions ({filename})",
                "markdown",
                True,
            )
            for filename in _gemini_context_names(_provider_config_dir(provider))
        ]
    if kind == "codex":
        home = _codex_home(provider)
        paths = [home / "AGENTS.md"]
        override = home / "AGENTS.override.md"
        if override.exists():
            paths.append(override)
        return [
            Candidate(
                path,
                "global",
                "instructions",
                _INSTRUCTIONS_CAPABILITY_ID,
                _INSTRUCTIONS_CAPABILITY_NAME,
                kind,
                name,
                f"Codex instructions ({path.name})",
                "markdown",
                True,
            )
            for path in paths
        ]
    return []


def _project_instruction_candidates(provider: dict, project_root: Path, cwd: str) -> list[Candidate]:
    kind = provider["kind"]
    name = provider.get("name") or kind
    dirs = _dirs_from_root(project_root, cwd)
    if kind == "claude":
        paths = [project_root / "CLAUDE.md"]
        for directory in dirs:
            paths.extend([directory / "CLAUDE.md", directory / "CLAUDE.local.md"])
        paths.append(project_root / ".claude" / "CLAUDE.md")
        return [
            Candidate(
                path,
                "project",
                "instructions",
                _INSTRUCTIONS_CAPABILITY_ID,
                _INSTRUCTIONS_CAPABILITY_NAME,
                kind,
                name,
                f"Claude instructions ({path.name})",
                "markdown",
                path == project_root / "CLAUDE.md",
            )
            for path in _dedupe_paths(paths)
            if path.exists() or path == project_root / "CLAUDE.md"
        ]
    if kind == "gemini":
        names = _gemini_context_names(_provider_config_dir(provider), project_root)
        paths = [project_root / "GEMINI.md"]
        paths.extend(directory / filename for directory in dirs for filename in names)
        return [
            Candidate(
                path,
                "project",
                "instructions",
                _INSTRUCTIONS_CAPABILITY_ID,
                _INSTRUCTIONS_CAPABILITY_NAME,
                kind,
                name,
                f"Gemini instructions ({path.name})",
                "markdown",
                path == project_root / "GEMINI.md",
            )
            for path in _dedupe_paths(paths)
            if path.exists() or path == project_root / "GEMINI.md"
        ]
    if kind == "codex":
        names = ["AGENTS.md", "AGENTS.override.md", *_codex_fallback_names(provider)]
        paths = [project_root / "AGENTS.md"]
        paths.extend(directory / filename for directory in dirs for filename in names)
        return [
            Candidate(
                path,
                "project",
                "instructions",
                _INSTRUCTIONS_CAPABILITY_ID,
                _INSTRUCTIONS_CAPABILITY_NAME,
                kind,
                name,
                f"Codex instructions ({path.name})",
                "markdown",
                path == project_root / "AGENTS.md",
            )
            for path in _dedupe_paths(paths)
            if path.exists() or path == project_root / "AGENTS.md"
        ]
    return []


def _project_auto_memory_candidates(provider: dict, project_root: Path) -> list[Candidate]:
    kind = provider["kind"]
    if kind != "claude":
        return []
    name = provider.get("name") or kind
    path = _provider_config_dir(provider) / "projects" / _encode_cwd_source(str(project_root)) / "memory" / "MEMORY.md"
    return [
        Candidate(
            path,
            "project",
            "memory",
            _MEMORY_CAPABILITY_ID,
            _MEMORY_CAPABILITY_NAME,
            kind,
            name,
            "Claude auto memory",
            "markdown",
            True,
        )
    ]


def _agents_skills_dir() -> Path:
    return _expand_path("~/.agents") / "skills"


def _skill_roots_for_provider(
    provider: dict,
    scope: str,
    project_root: Path | None = None,
    cwd: str = "",
) -> list[tuple[str, Path]]:
    kind = provider["kind"]
    if scope == "global":
        if kind == "claude":
            return [("", _provider_config_dir(provider) / "skills")]
        if kind == "gemini":
            return [("", _agents_skills_dir()), ("", _provider_config_dir(provider) / "skills")]
        if kind == "codex":
            return [("", _agents_skills_dir())]
        return []

    if project_root is None:
        return []
    dirs = _dirs_from_root(project_root, cwd)
    roots: list[tuple[str, Path]] = []
    for directory in dirs:
        rel = "." if directory == project_root else directory.relative_to(project_root).as_posix()
        if kind == "claude":
            roots.append((rel, directory / ".claude" / "skills"))
        elif kind == "gemini":
            roots.extend([(rel, directory / ".agents" / "skills"), (rel, directory / ".gemini" / "skills")])
        elif kind == "codex":
            roots.append((rel, directory / ".agents" / "skills"))
    return roots


def _skill_names_in_root(root: Path) -> set[str]:
    if not root.is_dir() or root.is_symlink():
        return set()
    names: set[str] = set()
    for child in root.iterdir():
        if (
            child.is_dir()
            and not child.is_symlink()
            and child.name not in {"", ".", ".."}
            and Path(child.name).name == child.name
            and _skill_file_for_dir(child) is not None
        ):
            names.add(child.name)
    return names


def _skill_file_for_dir(skill_dir: Path) -> Path | None:
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_file() and not skill_md.is_symlink():
        return skill_md
    named_md = skill_dir / f"{skill_dir.name}.md"
    if named_md.is_file() and not named_md.is_symlink():
        return named_md
    return None


def _global_skill_names(providers: list[dict]) -> set[str]:
    names: set[str] = set()
    for provider in providers:
        for _, root in _skill_roots_for_provider(provider, "global"):
            names.update(_skill_names_in_root(root))
    return names


def _project_skill_slots(providers: list[dict], project_root: Path, cwd: str) -> set[tuple[str, str]]:
    slots: set[tuple[str, str]] = set()
    for provider in providers:
        for rel, root in _skill_roots_for_provider(provider, "project", project_root, cwd):
            for name in _skill_names_in_root(root):
                slots.add((rel, name))
    return slots


def _skill_capability_id(rel: str, name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or hashlib.sha256(
        name.encode("utf-8")
    ).hexdigest()[:8]
    if rel in {"", "."}:
        return f"{_SKILL_CAPABILITY_PREFIX}{safe_name}"
    digest = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:8]
    return f"{_SKILL_CAPABILITY_PREFIX}{digest}-{safe_name}"


def _skill_capability_name(rel: str, name: str) -> str:
    if rel in {"", "."}:
        return f"Skill: {name}"
    return f"Skill: {name} ({rel})"


def _candidate_skill_paths(roots: list[tuple[str, Path]], rel: str, name: str) -> list[Path]:
    matching_roots = [root for root_rel, root in roots if root_rel == rel]
    existing = [
        skill_file
        for root in matching_roots
        if (skill_file := _skill_file_for_dir(root / name)) is not None
    ]
    if existing:
        return existing
    return [matching_roots[0] / name / "SKILL.md"] if matching_roots else []


def _global_skill_candidates(provider: dict, skill_names: set[str]) -> list[Candidate]:
    kind = provider["kind"]
    name = provider.get("name") or kind
    roots = _skill_roots_for_provider(provider, "global")
    candidates: list[Candidate] = []
    for skill_name in sorted(skill_names):
        for path in _candidate_skill_paths(roots, "", skill_name):
            candidates.append(
                Candidate(
                    path,
                    "global",
                    "skill",
                    _skill_capability_id("", skill_name),
                    _skill_capability_name("", skill_name),
                    kind,
                    name,
                    f"Skill ({skill_name})",
                    "json",
                    True,
                    _CONTENT_MARKDOWN_SKILL,
                )
            )
    return candidates


def _project_skill_candidates(
    provider: dict,
    project_root: Path,
    cwd: str,
    slots: set[tuple[str, str]],
) -> list[Candidate]:
    kind = provider["kind"]
    name = provider.get("name") or kind
    roots = _skill_roots_for_provider(provider, "project", project_root, cwd)
    candidates: list[Candidate] = []
    for rel, skill_name in sorted(slots):
        for path in _candidate_skill_paths(roots, rel, skill_name):
            candidates.append(
                Candidate(
                    path,
                    "project",
                    "skill",
                    _skill_capability_id(rel, skill_name),
                    _skill_capability_name(rel, skill_name),
                    kind,
                    name,
                    f"Skill ({skill_name})",
                    "json",
                    True,
                    _CONTENT_MARKDOWN_SKILL,
                )
            )
    return candidates


def _agent_roots_for_provider(
    provider: dict,
    scope: str,
    project_root: Path | None = None,
) -> list[Path]:
    kind = provider["kind"]
    if scope == "global":
        if kind == "claude":
            return [_provider_config_dir(provider) / "agents"]
        if kind == "gemini":
            return [_provider_config_dir(provider) / "agents"]
        if kind == "codex":
            return [_codex_home(provider) / "agents"]
        return []
    if project_root is None:
        return []
    if kind == "claude":
        return [project_root / ".claude" / "agents"]
    if kind == "gemini":
        return [project_root / ".gemini" / "agents"]
    if kind == "codex":
        return [project_root / ".codex" / "agents"]
    return []


def _agent_content_kind(provider_kind: str) -> str:
    return _CONTENT_TOML_AGENT if provider_kind == "codex" else _CONTENT_MARKDOWN_AGENT


def _agent_suffix(provider_kind: str) -> str:
    return ".toml" if provider_kind == "codex" else ".md"


def _agent_scan_patterns(content_kind: str) -> list[str]:
    suffix = ".toml" if content_kind == _CONTENT_TOML_AGENT else ".md"
    return [f"*{suffix}", f"*{suffix}.disabled"]


def _safe_agent_filename(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")
    if safe and safe not in {".", ".."} and Path(safe).name == safe:
        return safe
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]


def _agent_capability_id(name: str) -> str:
    return f"{_AGENT_CAPABILITY_PREFIX}{_safe_agent_filename(name)}"


def _agent_capability_name(name: str) -> str:
    return f"Custom agent: {name}"


def _command_roots_for_provider(
    provider: dict,
    scope: str,
    project_root: Path | None = None,
) -> list[Path]:
    kind = provider["kind"]
    if scope == "global":
        if kind == "claude":
            return [_provider_config_dir(provider) / "commands"]
        if kind == "gemini":
            return [_provider_config_dir(provider) / "commands"]
        if kind == "codex":
            return [_agents_skills_dir()]
        return []
    if project_root is None:
        return []
    if kind == "claude":
        return [project_root / ".claude" / "commands"]
    if kind == "gemini":
        return [project_root / ".gemini" / "commands"]
    if kind == "codex":
        return [project_root / ".agents" / "skills"]
    return []


def _command_content_kind(provider_kind: str) -> str:
    if provider_kind == "codex":
        return _CONTENT_CODEX_COMMAND_SKILL
    return _CONTENT_TOML_COMMAND if provider_kind == "gemini" else _CONTENT_MARKDOWN_COMMAND


def _command_suffix(provider_kind: str) -> str:
    return ".toml" if provider_kind == "gemini" else ".md"


def _command_capability_id(name: str) -> str:
    return f"{_COMMAND_CAPABILITY_PREFIX}{_safe_agent_filename(name)}"


def _command_capability_name(name: str) -> str:
    return f"Command/skill: {name}"


def _command_provider_label(provider_name: str, provider_kind: str) -> str:
    if provider_kind == "codex":
        return f"{provider_name} skill"
    return f"{provider_name} command"


def _codex_command_skill_dir(name: str) -> str:
    return f"{_CODEX_COMMAND_SKILL_PREFIX}{_safe_agent_filename(name)}"


def _codex_command_skill_name(name: str) -> str:
    return _codex_command_skill_dir(name)


def _frontmatter_split(path: Path, content: str) -> tuple[dict, str]:
    normalized = content.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        raise HTTPException(status_code=400, detail=f"agent file missing YAML frontmatter: {path}")
    end = normalized.find("\n---\n", 4)
    if end < 0:
        raise HTTPException(status_code=400, detail=f"agent file has unterminated YAML frontmatter: {path}")
    frontmatter = normalized[4:end]
    try:
        data = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"agent frontmatter is not valid YAML: {path}: {e}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail=f"agent frontmatter must be an object: {path}")
    return data, normalized[end + len("\n---\n") :]


def _normalized_item_payload(
    *,
    item_label: str,
    path: Path,
    name: object,
    description: object,
    instructions: object,
    metadata: dict,
) -> dict:
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail=f"{item_label} name must be a non-empty string: {path}")
    if not isinstance(description, str) or not description.strip():
        raise HTTPException(status_code=400, detail=f"{item_label} description must be a non-empty string: {path}")
    if not isinstance(instructions, str) or not instructions.strip():
        raise HTTPException(status_code=400, detail=f"{item_label} instructions must be a non-empty string: {path}")
    return {
        "name": name.strip(),
        "description": description.strip(),
        "instructions": instructions.strip() + "\n",
        "metadata": metadata,
    }


def _normalized_command_payload(
    *,
    path: Path,
    name: object,
    description: object,
    instructions: object,
    metadata: dict,
) -> dict:
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail=f"command name must be a non-empty string: {path}")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise HTTPException(status_code=400, detail=f"command description must be a string: {path}")
    if not isinstance(instructions, str) or not instructions.strip():
        raise HTTPException(status_code=400, detail=f"command instructions must be a non-empty string: {path}")
    return {
        "name": name.strip(),
        "description": description.strip(),
        "instructions": instructions.strip() + "\n",
        "metadata": metadata,
    }


def _normalized_item_text(payload: dict) -> str:
    ordered = {
        "name": payload["name"],
        "description": payload["description"],
        "instructions": payload["instructions"],
        "metadata": payload.get("metadata") or {},
    }
    return json.dumps(ordered, indent=2, ensure_ascii=False) + "\n"


def _markdown_agent_payload(path: Path, content: str) -> dict:
    data, body = _frontmatter_split(path, content)
    metadata = {k: v for k, v in data.items() if k not in {"name", "description"}}
    return _normalized_item_payload(
        item_label="agent",
        path=path,
        name=data.get("name"),
        description=data.get("description"),
        instructions=body,
        metadata=metadata,
    )


def _markdown_skill_payload(path: Path, content: str) -> dict:
    data, body = _frontmatter_split(path, content)
    metadata = {k: v for k, v in data.items() if k not in {"name", "description"}}
    return _normalized_item_payload(
        item_label="skill",
        path=path,
        name=data.get("name") or path.parent.name,
        description=data.get("description"),
        instructions=body,
        metadata=metadata,
    )


def _markdown_command_payload(path: Path, content: str) -> dict:
    metadata: dict = {}
    description = ""
    body = content
    if content.replace("\r\n", "\n").startswith("---\n"):
        data, body = _frontmatter_split(path, content)
        metadata = {k: v for k, v in data.items() if k != "description"}
        description = data.get("description") or ""
    return _normalized_command_payload(
        path=path,
        name=path.stem,
        description=description,
        instructions=body,
        metadata=metadata,
    )


def _codex_command_skill_payload(path: Path, content: str) -> dict:
    payload = _markdown_skill_payload(path, content)
    metadata = payload.get("metadata") or {}
    if metadata.get(_CODEX_COMMAND_SKILL_KIND_KEY) != "command":
        raise HTTPException(status_code=400, detail=f"Codex command skill missing command marker: {path}")
    command_name = metadata.get(_CODEX_COMMAND_SKILL_NAME_KEY)
    if not isinstance(command_name, str) or not command_name.strip():
        raise HTTPException(status_code=400, detail=f"Codex command skill missing command name: {path}")
    command_description = metadata.get(_CODEX_COMMAND_SKILL_DESCRIPTION_KEY, payload["description"])
    if not isinstance(command_description, str):
        raise HTTPException(status_code=400, detail=f"Codex command skill description marker must be a string: {path}")
    command_metadata = {
        key: value
        for key, value in metadata.items()
        if key
        not in {
            _CODEX_COMMAND_SKILL_KIND_KEY,
            _CODEX_COMMAND_SKILL_NAME_KEY,
            _CODEX_COMMAND_SKILL_DESCRIPTION_KEY,
        }
    }
    return _normalized_command_payload(
        path=path,
        name=command_name,
        description=command_description,
        instructions=payload["instructions"],
        metadata=command_metadata,
    )


def _toml_agent_payload(path: Path, content: str) -> dict:
    data = _toml_object_from_text(path, content)
    metadata = {k: v for k, v in data.items() if k not in {"name", "description", "developer_instructions"}}
    return _normalized_item_payload(
        item_label="agent",
        path=path,
        name=data.get("name"),
        description=data.get("description"),
        instructions=data.get("developer_instructions"),
        metadata=metadata,
    )


def _toml_command_payload(path: Path, content: str) -> dict:
    data = _toml_object_from_text(path, content)
    metadata = {k: v for k, v in data.items() if k not in {"description", "prompt"}}
    return _normalized_command_payload(
        path=path,
        name=path.stem,
        description=data.get("description") or "",
        instructions=data.get("prompt"),
        metadata=metadata,
    )


def _item_payload_from_normalized(content: str, item_label: str) -> dict:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"{item_label} content must be valid JSON: {e.msg}")
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"{item_label} content must be a JSON object")
    metadata = value.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="metadata must be an object")
    if item_label == "command":
        return _normalized_command_payload(
            path=Path("<command>"),
            name=value.get("name"),
            description=value.get("description"),
            instructions=value.get("instructions"),
            metadata=metadata,
        )
    return _normalized_item_payload(
        item_label=item_label,
        path=Path(f"<{item_label}>"),
        name=value.get("name"),
        description=value.get("description"),
        instructions=value.get("instructions"),
        metadata=metadata,
    )


def _agent_name_from_file(path: Path, content_kind: str) -> str | None:
    try:
        content = _read_existing_text(path)
        if content is None:
            return None
        if content_kind == _CONTENT_MARKDOWN_AGENT:
            return _markdown_agent_payload(path, content)["name"]
        if content_kind == _CONTENT_TOML_AGENT:
            return _toml_agent_payload(path, content)["name"]
    except HTTPException:
        return None
    return None


def _agent_names_in_root(root: Path, content_kind: str) -> set[str]:
    if not root.is_dir() or root.is_symlink():
        return set()
    names: set[str] = set()
    for pattern in _agent_scan_patterns(content_kind):
        for path in root.rglob(pattern):
            if path.is_file() and not path.is_symlink():
                name = _agent_name_from_file(path, content_kind)
                if name:
                    names.add(name)
    return names


def _agent_names(providers: list[dict], scope: str, project_root: Path | None = None) -> set[str]:
    names: set[str] = set()
    for provider in providers:
        content_kind = _agent_content_kind(provider["kind"])
        for root in _agent_roots_for_provider(provider, scope, project_root):
            names.update(_agent_names_in_root(root, content_kind))
    return names


def _candidate_agent_paths(provider: dict, roots: list[Path], name: str) -> list[Path]:
    content_kind = _agent_content_kind(provider["kind"])
    suffix = _agent_suffix(provider["kind"])
    existing: list[Path] = []
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for pattern in _agent_scan_patterns(content_kind):
            for path in root.rglob(pattern):
                if path.is_file() and not path.is_symlink() and _agent_name_from_file(path, content_kind) == name:
                    existing.append(path)
    if existing:
        return existing
    return [roots[0] / f"{_safe_agent_filename(name)}{suffix}"] if roots else []


def _agent_candidates(
    provider: dict,
    scope: str,
    names: set[str],
    project_root: Path | None = None,
) -> list[Candidate]:
    kind = provider["kind"]
    provider_name = provider.get("name") or kind
    roots = _agent_roots_for_provider(provider, scope, project_root)
    candidates: list[Candidate] = []
    for agent_name in sorted(names):
        for path in _candidate_agent_paths(provider, roots, agent_name):
            candidates.append(
                Candidate(
                    path,
                    scope,
                    "agent",
                    _agent_capability_id(agent_name),
                    _agent_capability_name(agent_name),
                    kind,
                    provider_name,
                    f"{provider_name} agent",
                    "json",
                    True,
                    _agent_content_kind(kind),
                )
            )
    return candidates


def _command_names_in_root(root: Path, suffix: str) -> set[str]:
    if not root.is_dir() or root.is_symlink():
        return set()
    return {
        path.stem
        for path in root.iterdir()
        if path.is_file()
        and not path.is_symlink()
        and path.suffix == suffix
        and path.stem not in {"", ".", ".."}
        and Path(path.stem).name == path.stem
    }


def _codex_command_names_in_root(root: Path) -> set[str]:
    if not root.is_dir() or root.is_symlink():
        return set()
    names: set[str] = set()
    for child in root.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        skill_file = _skill_file_for_dir(child)
        if skill_file is None:
            continue
        try:
            content = _read_existing_text(skill_file)
            if content is None:
                continue
            names.add(_codex_command_skill_payload(skill_file, content)["name"])
        except HTTPException:
            continue
    return names


def _command_names(providers: list[dict], scope: str, project_root: Path | None = None) -> set[str]:
    names: set[str] = set()
    for provider in providers:
        kind = provider["kind"]
        for root in _command_roots_for_provider(provider, scope, project_root):
            if kind == "codex":
                names.update(_codex_command_names_in_root(root))
            else:
                names.update(_command_names_in_root(root, _command_suffix(kind)))
    return names


def _candidate_command_paths(provider: dict, roots: list[Path], name: str) -> list[Path]:
    if provider["kind"] == "codex":
        existing: list[Path] = []
        for root in roots:
            path = root / _codex_command_skill_dir(name) / "SKILL.md"
            if path.is_file() and not path.is_symlink():
                existing.append(path)
        if existing:
            return existing
        return [roots[0] / _codex_command_skill_dir(name) / "SKILL.md"] if roots else []
    suffix = _command_suffix(provider["kind"])
    existing = [
        root / f"{name}{suffix}"
        for root in roots
        if (root / f"{name}{suffix}").is_file()
        and not (root / f"{name}{suffix}").is_symlink()
    ]
    if existing:
        return existing
    return [roots[0] / f"{_safe_agent_filename(name)}{suffix}"] if roots else []


def _command_candidates(
    provider: dict,
    scope: str,
    names: set[str],
    project_root: Path | None = None,
) -> list[Candidate]:
    kind = provider["kind"]
    provider_name = provider.get("name") or kind
    roots = _command_roots_for_provider(provider, scope, project_root)
    candidates: list[Candidate] = []
    for command_name in sorted(names):
        for path in _candidate_command_paths(provider, roots, command_name):
            candidates.append(
                Candidate(
                    path,
                    scope,
                    "command",
                    _command_capability_id(command_name),
                    _command_capability_name(command_name),
                    kind,
                    provider_name,
                    _command_provider_label(provider_name, kind),
                    "json",
                    True,
                    _command_content_kind(kind),
                )
            )
    return candidates


def _global_config_candidates(provider: dict) -> list[Candidate]:
    kind = provider["kind"]
    name = provider.get("name") or kind
    if kind == "claude":
        config_dir = _provider_config_dir(provider)
        candidates = [
            Candidate(
                config_dir / "settings.json",
                "global",
                "config",
                "settings",
                "Provider settings",
                kind,
                name,
                "Claude settings",
                "json",
                True,
            ),
        ]
        local_settings = config_dir / "settings.local.json"
        if local_settings.exists():
            candidates.append(
                Candidate(
                    local_settings,
                    "global",
                    "config",
                    "settings",
                    "Provider settings",
                    kind,
                    name,
                    "Claude local settings",
                    "json",
                    False,
                )
            )
        return candidates
    if kind == "gemini":
        path = _provider_config_dir(provider) / "settings.json"
        return [
            Candidate(
                path,
                "global",
                "config",
                "settings",
                "Provider settings",
                kind,
                name,
                "Gemini settings",
                "json",
                True,
            ),
            Candidate(
                path,
                "global",
                "config",
                _MCP_CAPABILITY_ID,
                _MCP_CAPABILITY_NAME,
                kind,
                name,
                "Gemini MCP",
                "json",
                True,
                _CONTENT_JSON_MCP,
            ),
        ]
    if kind == "codex":
        path = _codex_home(provider) / "config.toml"
        return [
            Candidate(
                path,
                "global",
                "config",
                "settings",
                "Provider settings",
                kind,
                name,
                "Codex config",
                "toml",
                True,
            ),
            Candidate(
                path,
                "global",
                "config",
                _MCP_CAPABILITY_ID,
                _MCP_CAPABILITY_NAME,
                kind,
                name,
                "Codex MCP",
                "json",
                True,
                _CONTENT_TOML_MCP,
            ),
        ]
    return []


def _project_config_candidates(provider: dict, project_root: Path) -> list[Candidate]:
    kind = provider["kind"]
    name = provider.get("name") or kind
    if kind == "claude":
        candidates = [
            Candidate(
                project_root / ".claude" / "settings.json",
                "project",
                "config",
                "settings",
                "Provider settings",
                kind,
                name,
                "Claude settings",
                "json",
                True,
            ),
            Candidate(
                project_root / ".mcp.json",
                "project",
                "config",
                _MCP_CAPABILITY_ID,
                _MCP_CAPABILITY_NAME,
                kind,
                name,
                "Claude MCP",
                "json",
                True,
            ),
        ]
        local_settings = project_root / ".claude" / "settings.local.json"
        if local_settings.exists():
            candidates.append(
                Candidate(
                    local_settings,
                    "project",
                    "config",
                    "settings",
                    "Provider settings",
                    kind,
                    name,
                    "Claude local settings",
                    "json",
                    False,
                )
            )
        return candidates
    if kind == "gemini":
        path = project_root / ".gemini" / "settings.json"
        return [
            Candidate(
                path,
                "project",
                "config",
                "settings",
                "Provider settings",
                kind,
                name,
                "Gemini settings",
                "json",
                True,
            ),
            Candidate(
                path,
                "project",
                "config",
                _MCP_CAPABILITY_ID,
                _MCP_CAPABILITY_NAME,
                kind,
                name,
                "Gemini MCP",
                "json",
                True,
                _CONTENT_JSON_MCP,
            ),
        ]
    if kind == "codex":
        return [
            Candidate(
                project_root / ".codex" / "config.toml",
                "project",
                "config",
                _MCP_CAPABILITY_ID,
                _MCP_CAPABILITY_NAME,
                kind,
                name,
                "Codex MCP",
                "json",
                True,
                _CONTENT_TOML_MCP,
            )
        ]
    return []


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path.absolute())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _backup_path(path: Path) -> Path:
    return path.with_name(path.name + _BACKUP_SUFFIX)


def _backup_marker_path(backup: Path) -> Path:
    return backup.with_name(backup.name + _BACKUP_MARKER_SUFFIX)


def _real_existing_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    try:
        real = path.resolve(strict=True)
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"path changed: {e}")
    if not real.is_file() or real.is_symlink():
        raise HTTPException(status_code=400, detail=f"path is not a regular file: {path}")
    try:
        st = real.stat(follow_symlinks=False)
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"path changed: {e}")
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise HTTPException(status_code=400, detail=f"path is not safely editable: {path}")
    return real


def _read_existing_text(path: Path) -> str | None:
    real = _real_existing_file(path)
    if real is None:
        return None
    try:
        return real.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail=f"path is not UTF-8: {path}")
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"path changed: {e}")


def _read_entry_content(path: Path) -> tuple[str, str | None, bool]:
    try:
        content = _read_existing_text(path)
    except HTTPException as e:
        return "", str(e.detail), False
    return content or "", None, content is not None


def _json_object_from_text(path: Path, content: str) -> dict:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"path is not valid JSON: {path}: {e.msg}")
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"path is not a JSON object: {path}")
    return value


def _toml_object_from_text(path: Path, content: str) -> dict:
    try:
        value = tomllib.loads(content)
    except tomllib.TOMLDecodeError as e:
        raise HTTPException(status_code=400, detail=f"path is not valid TOML: {path}: {e}")
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail=f"path is not a TOML object: {path}")
    return value


def _mcp_fragment_from_servers(path: Path, servers: object) -> str:
    if not isinstance(servers, dict):
        raise HTTPException(status_code=400, detail=f"MCP servers must be an object: {path}")
    try:
        return json.dumps({"mcpServers": servers}, indent=2, sort_keys=True) + "\n"
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"MCP servers are not JSON-compatible: {path}: {e}")


def _mcp_servers_from_fragment(content: str) -> dict:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"MCP content must be valid JSON: {e.msg}")
    if not isinstance(value, dict) or not isinstance(value.get("mcpServers"), dict):
        raise HTTPException(status_code=400, detail="MCP content must be a JSON object with an mcpServers object")
    return value["mcpServers"]


def _json_mcp_current(path: Path) -> tuple[str, bool]:
    content = _read_existing_text(path)
    if content is None:
        return "", False
    data = _json_object_from_text(path, content)
    if "mcpServers" not in data:
        return "", False
    return _mcp_fragment_from_servers(path, data["mcpServers"]), True


def _toml_mcp_current(path: Path) -> tuple[str, bool]:
    content = _read_existing_text(path)
    if content is None:
        return "", False
    data = _toml_object_from_text(path, content)
    if "mcp_servers" not in data:
        return "", False
    return _mcp_fragment_from_servers(path, data["mcp_servers"]), True


def _markdown_agent_current(path: Path) -> tuple[str, bool]:
    content = _read_existing_text(path)
    if content is None:
        return "", False
    return _normalized_item_text(_markdown_agent_payload(path, content)), True


def _markdown_skill_current(path: Path) -> tuple[str, bool]:
    content = _read_existing_text(path)
    if content is None:
        return "", False
    return _normalized_item_text(_markdown_skill_payload(path, content)), True


def _toml_agent_current(path: Path) -> tuple[str, bool]:
    content = _read_existing_text(path)
    if content is None:
        return "", False
    return _normalized_item_text(_toml_agent_payload(path, content)), True


def _markdown_command_current(path: Path) -> tuple[str, bool]:
    content = _read_existing_text(path)
    if content is None:
        return "", False
    return _normalized_item_text(_markdown_command_payload(path, content)), True


def _toml_command_current(path: Path) -> tuple[str, bool]:
    content = _read_existing_text(path)
    if content is None:
        return "", False
    return _normalized_item_text(_toml_command_payload(path, content)), True


def _codex_command_skill_current(path: Path) -> tuple[str, bool]:
    content = _read_existing_text(path)
    if content is None:
        return "", False
    return _normalized_item_text(_codex_command_skill_payload(path, content)), True


def _file_current(path: Path) -> tuple[str, bool]:
    content = _read_existing_text(path)
    return content or "", content is not None


def _read_candidate_content(candidate: Candidate) -> tuple[str, str | None, bool]:
    try:
        content, exists = _content_adapter(candidate.content_kind).read_current(candidate.path)
        return content, None, exists
    except HTTPException as e:
        return "", str(e.detail), False


def _file_mode(category: str) -> int:
    return 0o600 if category == "config" else 0o644


_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def _estimate_tokens(content: str) -> int:
    if not content.strip():
        return 0
    lexical = len(_TOKEN_RE.findall(content))
    char_floor = math.ceil(len(content) / 4)
    return max(1, lexical, char_floor)


def _entry_id_from_candidate(candidate: Candidate) -> str:
    path = candidate.path.absolute()
    return ":".join(
        [
            candidate.scope,
            candidate.category,
            candidate.capability_id,
            candidate.content_kind,
            str(path),
        ]
    )


def _entry_from_candidate(candidate: Candidate) -> dict:
    path = candidate.path.absolute()
    path_key = str(path)
    content, error, exists = _read_candidate_content(candidate)
    token_count = _estimate_tokens(content) if exists and error is None else 0
    return {
        "entry_id": _entry_id_from_candidate(candidate),
        "path": path_key,
        "content_kind": candidate.content_kind,
        "scope": candidate.scope,
        "category": candidate.category,
        "capability_id": candidate.capability_id,
        "capability_key": _capability_key(candidate.scope, candidate.category, candidate.capability_id),
        "capability_name": candidate.capability_name,
        "role": "specific",
        "label": candidate.label,
        "language": candidate.language,
        "content": content,
        "token_count": token_count,
        "exists": exists,
        "read_error": error,
        "writable": error is None and (exists or candidate.can_create),
        "backup_exists": _backup_exists(path),
        "provider_names": [candidate.provider_name],
        "provider_kinds": [candidate.provider_kind],
    }


def _backup_exists(path: Path) -> bool:
    try:
        real = _real_existing_file(path)
    except HTTPException:
        return False
    if real is not None:
        path = real
    backup = _backup_path(path)
    marker = _backup_marker_path(backup)
    return (
        backup.is_file()
        and not backup.is_symlink()
        and marker.is_file()
        and not marker.is_symlink()
    )


def _merge_entry(entry: dict, by_entry: dict[str, dict]) -> None:
    current = by_entry.setdefault(entry["entry_id"], entry)
    if entry["read_error"] and not current["read_error"]:
        current["read_error"] = entry["read_error"]
        current["writable"] = False
    if entry["exists"]:
        current["exists"] = True
        current["content"] = entry["content"]
        current["token_count"] = entry["token_count"]
    if entry["writable"] and not current["read_error"]:
        current["writable"] = True
    for provider_name in entry["provider_names"]:
        if provider_name not in current["provider_names"]:
            current["provider_names"].append(provider_name)
    for provider_kind in entry["provider_kinds"]:
        if provider_kind not in current["provider_kinds"]:
            current["provider_kinds"].append(provider_kind)


def _entry_from_candidates(candidate: Candidate, by_entry: dict[str, dict]) -> None:
    entry = _entry_from_candidate(candidate)
    _merge_entry(entry, by_entry)


def _capability_key(scope: str, category: str, capability_id: str) -> str:
    return f"{scope}:{category}:{capability_id}"


def _capability_language(category: str, specifics: list[dict]) -> str:
    if category in {"instructions", "memory"}:
        return "markdown"
    if category in {"agent", "skill", "command"}:
        return "json"
    languages = {entry["language"] for entry in specifics}
    if len(languages) == 1:
        return next(iter(languages))
    return "plaintext"


def _capability_extension(language: str) -> str:
    return {
        "markdown": "md",
        "json": "json",
        "toml": "toml",
    }.get(language, "txt")


def _capability_unified_path(
    scope: str,
    category: str,
    capability_id: str,
    language: str,
    project_root: Path | None,
) -> Path:
    if scope == "global":
        root = _sync_home_source() / "provider-config-sync" / "global"
    else:
        if project_root is None:
            raise HTTPException(status_code=400, detail="project capability requires project root")
        digest = hashlib.sha256(str(project_root).encode("utf-8")).hexdigest()
        root = _sync_home_source() / "provider-config-sync" / "projects" / digest
    return root / category / f"{capability_id}.{_capability_extension(language)}"


def _unified_root(scope: str, project_root: Path | None) -> Path:
    if scope == "global":
        return _sync_home_source() / "provider-config-sync" / "global"
    if project_root is None:
        raise HTTPException(status_code=400, detail="project capability requires project root")
    digest = hashlib.sha256(str(project_root).encode("utf-8")).hexdigest()
    return _sync_home_source() / "provider-config-sync" / "projects" / digest


def _language_from_unified_path(path: Path) -> str | None:
    return {
        ".json": "json",
        ".md": "markdown",
        ".toml": "toml",
        ".txt": "plaintext",
    }.get(path.suffix)


def _capability_name_from_id(category: str, capability_id: str) -> str:
    if category == "instructions" and capability_id == _INSTRUCTIONS_CAPABILITY_ID:
        return _INSTRUCTIONS_CAPABILITY_NAME
    if category == "memory" and capability_id == _MEMORY_CAPABILITY_ID:
        return _MEMORY_CAPABILITY_NAME
    if category == "config" and capability_id == _MCP_CAPABILITY_ID:
        return _MCP_CAPABILITY_NAME
    if category == "skill" and capability_id.startswith(_SKILL_CAPABILITY_PREFIX):
        return _skill_capability_name("", capability_id.removeprefix(_SKILL_CAPABILITY_PREFIX))
    if category == "agent" and capability_id.startswith(_AGENT_CAPABILITY_PREFIX):
        return _agent_capability_name(capability_id.removeprefix(_AGENT_CAPABILITY_PREFIX))
    if category == "command" and capability_id.startswith(_COMMAND_CAPABILITY_PREFIX):
        return _command_capability_name(capability_id.removeprefix(_COMMAND_CAPABILITY_PREFIX))
    return capability_id


def _unified_capability_items(scope: str, project_root: Path | None) -> list[dict]:
    root = _unified_root(scope, project_root)
    if not root.is_dir() or root.is_symlink():
        return []
    items: list[dict] = []
    for category_dir in sorted(root.iterdir(), key=lambda path: path.name):
        if not category_dir.is_dir() or category_dir.is_symlink():
            continue
        category = category_dir.name
        for path in sorted(category_dir.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.is_symlink():
                continue
            language = _language_from_unified_path(path)
            if language is None:
                continue
            capability_id = path.stem
            items.append(
                {
                    "scope": scope,
                    "category": category,
                    "capability_id": capability_id,
                    "capability_name": _capability_name_from_id(category, capability_id),
                    "specifics": [],
                    "language": language,
                }
            )
    return items


def _unified_entry(
    *,
    scope: str,
    category: str,
    capability_id: str,
    capability_name: str,
    language: str,
    project_root: Path | None,
) -> dict:
    path = _capability_unified_path(scope, category, capability_id, language, project_root).absolute()
    content, error, exists = _read_entry_content(path)
    token_count = _estimate_tokens(content) if exists and error is None else 0
    entry_id = ":".join(["unified", scope, category, capability_id, str(path)])
    return {
        "entry_id": entry_id,
        "path": str(path),
        "content_kind": _CONTENT_FILE,
        "scope": scope,
        "category": category,
        "capability_id": capability_id,
        "capability_key": _capability_key(scope, category, capability_id),
        "capability_name": capability_name,
        "role": "unified",
        "label": f"{capability_name} unified",
        "language": language,
        "content": content,
        "token_count": token_count,
        "exists": exists,
        "read_error": error,
        "writable": error is None,
        "backup_exists": _backup_exists(path),
        "provider_names": ["Unified"],
        "provider_kinds": ["unified"],
    }


def _capability_from_item(
    *,
    scope: str,
    category: str,
    capability_id: str,
    capability_name: str,
    specifics: list[dict],
    project_root: Path | None,
    language: str,
) -> dict:
    unified = _unified_entry(
        scope=scope,
        category=category,
        capability_id=capability_id,
        capability_name=capability_name,
        language=language,
        project_root=project_root,
    )
    provider_token_counts = _provider_token_counts(specifics)
    specific_token_count = sum(entry["token_count"] for entry in specifics)
    return {
        "id": _capability_key(scope, category, capability_id),
        "capability_id": capability_id,
        "name": capability_name,
        "scope": scope,
        "category": category,
        "language": language,
        "unified": unified,
        "specifics": specifics,
        "unified_token_count": unified["token_count"],
        "specific_token_count": specific_token_count,
        "total_token_count": unified["token_count"] + specific_token_count,
        "provider_token_counts": provider_token_counts,
        "has_diffs": any(entry["content"] != unified["content"] for entry in specifics),
        "specific_count": len(specifics),
        "missing_count": sum(1 for entry in specifics if not entry["exists"]),
    }


def _provider_token_counts(entries: list[dict]) -> list[dict]:
    by_key: dict[tuple[str, str], int] = {}
    for entry in entries:
        for provider_kind, provider_name in zip(entry["provider_kinds"], entry["provider_names"], strict=False):
            key = (provider_kind, provider_name)
            by_key[key] = by_key.get(key, 0) + entry["token_count"]
    return [
        {"provider_kind": kind, "provider_name": name, "token_count": count}
        for (kind, name), count in sorted(by_key.items(), key=lambda item: (item[0][0], item[0][1]))
    ]


def _token_totals(capabilities: list[dict]) -> dict:
    unified = sum(capability["unified_token_count"] for capability in capabilities)
    specifics = sum(capability["specific_token_count"] for capability in capabilities)
    provider_counts: dict[tuple[str, str], int] = {}
    for capability in capabilities:
        for item in capability["provider_token_counts"]:
            key = (item["provider_kind"], item["provider_name"])
            provider_counts[key] = provider_counts.get(key, 0) + item["token_count"]
    return {
        "unified": unified,
        "specifics": specifics,
        "all_tracked": unified + specifics,
        "by_provider": [
            {"provider_kind": kind, "provider_name": name, "token_count": count}
            for (kind, name), count in sorted(provider_counts.items(), key=lambda item: (item[0][0], item[0][1]))
        ],
    }


_AUTO_SYNC_OPERATIONS = ("additive", "removal", "change")
_AUTO_SYNC_MODES = ("off", "auto", "review", "llm")
_AUTO_SYNC_DEFAULT_POLICY = {operation: "off" for operation in _AUTO_SYNC_OPERATIONS}


def _settings_path() -> Path:
    return _sync_home_source() / "provider-config-sync" / "settings.json"


def _clean_auto_sync_policy(raw: object, *, allow_partial: bool) -> dict:
    if not isinstance(raw, dict):
        return {} if allow_partial else dict(_AUTO_SYNC_DEFAULT_POLICY)
    cleaned: dict[str, str] = {}
    for operation in _AUTO_SYNC_OPERATIONS:
        value = raw.get(operation)
        if isinstance(value, str) and value in _AUTO_SYNC_MODES:
            cleaned[operation] = value
        elif not allow_partial:
            cleaned[operation] = "off"
    return cleaned


def _clean_auto_sync_settings(raw: object) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    projects: dict[str, dict] = {}
    for cwd, project_value in (raw.get("projects") or {}).items():
        if not isinstance(cwd, str) or not cwd or not isinstance(project_value, dict):
            continue
        project_capabilities: dict[str, dict] = {}
        for capability_id, policy in (project_value.get("capabilities") or {}).items():
            if not isinstance(capability_id, str) or not _valid_capability_id(capability_id):
                continue
            cleaned = _clean_auto_sync_policy(policy, allow_partial=True)
            if cleaned:
                project_capabilities[capability_id] = cleaned
        project_policy = _clean_auto_sync_policy(project_value.get("policy"), allow_partial=True)
        project_entry: dict[str, object] = {}
        if project_policy:
            project_entry["policy"] = project_policy
        if project_capabilities:
            project_entry["capabilities"] = project_capabilities
        if project_entry:
            projects[cwd] = project_entry
    capabilities: dict[str, dict] = {}
    for capability_id, policy in (raw.get("capabilities") or {}).items():
        if not isinstance(capability_id, str) or not _valid_capability_id(capability_id):
            continue
        cleaned = _clean_auto_sync_policy(policy, allow_partial=True)
        if cleaned:
            capabilities[capability_id] = cleaned
    return {
        "global": _clean_auto_sync_policy(raw.get("global"), allow_partial=False),
        "capabilities": capabilities,
        "projects": projects,
    }


def _read_auto_sync_settings() -> dict:
    path = _settings_path()
    if not path.exists():
        return _clean_auto_sync_settings({})
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=409, detail=f"provider sync settings are unreadable: {e}")
    return _clean_auto_sync_settings(raw.get("auto_sync") if isinstance(raw, dict) else {})


def _write_auto_sync_settings(settings: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"auto_sync": settings}, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as fh:
        fh.write(payload)
        tmp_path = Path(fh.name)
    try:
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _valid_capability_id(capability_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", capability_id))


def _known_local_project_paths() -> set[str]:
    return {
        str(_expand_path(project.get("path", "")).resolve())
        for project in _project_records_source()
        if isinstance(project.get("path"), str) and project.get("path")
    }


def _normalize_settings_cwd(cwd: str) -> str:
    if not cwd:
        return ""
    resolved = str(_local_project_root(cwd))
    known = _known_local_project_paths()
    if known and resolved not in known:
        raise HTTPException(status_code=400, detail="unknown local project cwd")
    return resolved


def _effective_auto_sync_policy(settings: dict, cwd: str = "", capability_id: str = "") -> dict:
    policy = dict(_AUTO_SYNC_DEFAULT_POLICY)
    policy.update(settings["global"])
    if capability_id:
        policy.update(settings["capabilities"].get(capability_id, {}))
    if cwd:
        project = settings["projects"].get(cwd, {})
        policy.update(project.get("policy", {}))
        if capability_id:
            policy.update((project.get("capabilities") or {}).get(capability_id, {}))
    return policy


def get_auto_sync_settings(cwd: str = "", capability_id: str = "") -> dict:
    normalized_cwd = _normalize_settings_cwd(cwd)
    if capability_id and not _valid_capability_id(capability_id):
        raise HTTPException(status_code=400, detail="invalid provider sync capability id")
    settings = _read_auto_sync_settings()
    return {
        **settings,
        "effective": _effective_auto_sync_policy(settings, normalized_cwd, capability_id),
    }


def update_auto_sync_settings(req: AutoSyncSettingsPatch) -> dict:
    if req.level not in {"global", "capability", "project", "project_capability"}:
        raise HTTPException(status_code=400, detail="invalid auto-sync settings level")
    capability_id = req.capability_id
    if capability_id and not _valid_capability_id(capability_id):
        raise HTTPException(status_code=400, detail="invalid provider sync capability id")
    cwd = _normalize_settings_cwd(req.cwd)
    settings = _read_auto_sync_settings()
    cleaned = _clean_auto_sync_policy(req.policy, allow_partial=req.level != "global")
    if req.level == "global":
        settings["global"] = cleaned
    elif req.level == "capability":
        if not capability_id:
            raise HTTPException(status_code=400, detail="capability_id is required")
        if cleaned:
            settings["capabilities"][capability_id] = cleaned
        else:
            settings["capabilities"].pop(capability_id, None)
    elif req.level == "project":
        if not cwd:
            raise HTTPException(status_code=400, detail="cwd is required")
        project = settings["projects"].setdefault(cwd, {})
        if cleaned:
            project["policy"] = cleaned
        else:
            project.pop("policy", None)
        if not project.get("policy") and not project.get("capabilities"):
            settings["projects"].pop(cwd, None)
    else:
        if not cwd or not capability_id:
            raise HTTPException(status_code=400, detail="cwd and capability_id are required")
        project = settings["projects"].setdefault(cwd, {})
        capabilities = project.setdefault("capabilities", {})
        if cleaned:
            capabilities[capability_id] = cleaned
        else:
            capabilities.pop(capability_id, None)
        if not capabilities:
            project.pop("capabilities", None)
        if not project.get("policy") and not project.get("capabilities"):
            settings["projects"].pop(cwd, None)
    _write_auto_sync_settings(settings)
    return {
        **settings,
        "effective": _effective_auto_sync_policy(settings, cwd, capability_id),
    }


def _discover(cwd: str) -> dict:
    by_entry: dict[str, dict] = {}
    project_root: Path | None = None
    providers = _provider_records()
    global_skill_names = _global_skill_names(providers)
    global_agent_names = _agent_names(providers, "global")
    global_command_names = _command_names(providers, "global")
    for provider in providers:
        for candidate in [
            *_global_instruction_candidates(provider),
            *_global_config_candidates(provider),
            *_global_skill_candidates(provider, global_skill_names),
            *_agent_candidates(provider, "global", global_agent_names),
            *_command_candidates(provider, "global", global_command_names),
        ]:
            _entry_from_candidates(candidate, by_entry)

    if cwd:
        try:
            project_root = _local_project_root(cwd)
        except HTTPException:
            project_root = None
        if project_root is not None:
            project_skill_slots = _project_skill_slots(providers, project_root, cwd)
            project_agent_names = _agent_names(providers, "project", project_root)
            project_command_names = _command_names(providers, "project", project_root)
            for provider in providers:
                candidates = [
                    *_project_instruction_candidates(provider, project_root, cwd),
                    *_project_auto_memory_candidates(provider, project_root),
                    *_project_config_candidates(provider, project_root),
                    *_project_skill_candidates(provider, project_root, cwd, project_skill_slots),
                    *_agent_candidates(provider, "project", project_agent_names, project_root),
                    *_command_candidates(provider, "project", project_command_names, project_root),
                ]
                for candidate in candidates:
                    _entry_from_candidates(candidate, by_entry)

    specifics = [by_entry[key] for key in sorted(by_entry)]
    by_capability: dict[str, dict] = {}
    for entry in specifics:
        item = by_capability.setdefault(
            entry["capability_key"],
            {
                "scope": entry["scope"],
                "category": entry["category"],
                "capability_id": entry["capability_id"],
                "capability_name": entry["capability_name"],
                "specifics": [],
            },
        )
        item["specifics"].append(entry)
    for item in _unified_capability_items("global", None):
        by_capability.setdefault(_capability_key(item["scope"], item["category"], item["capability_id"]), item)
    if project_root is not None:
        for item in _unified_capability_items("project", project_root):
            by_capability.setdefault(_capability_key(item["scope"], item["category"], item["capability_id"]), item)

    capabilities = [
        _capability_from_item(
            scope=item["scope"],
            category=item["category"],
            capability_id=item["capability_id"],
            capability_name=item["capability_name"],
            specifics=item["specifics"],
            project_root=project_root if item["scope"] == "project" else None,
            language=item.get("language") or _capability_language(item["category"], item["specifics"]),
        )
        for item in sorted(by_capability.values(), key=lambda item: (item["scope"], item["capability_name"]))
    ]
    files = [capability["unified"] for capability in capabilities]
    for capability in capabilities:
        files.extend(capability["specifics"])
    normalized_cwd = str(project_root) if project_root is not None else ""
    return {
        "files": files,
        "capabilities": capabilities,
        "providers": [
            {"kind": provider["kind"], "name": provider.get("name") or provider["kind"]}
            for provider in providers
        ],
        "token_totals": _token_totals(capabilities),
        "groups": {
            scope: [
                capability
                for capability in capabilities
                if capability["scope"] == scope
            ]
            for scope in ("global", "project")
        },
        "auto_settings": get_auto_sync_settings(normalized_cwd),
    }


def _entry_map(cwd: str) -> dict[str, dict]:
    entries = _discover(cwd)["files"]
    by_key = {entry["entry_id"]: entry for entry in entries}
    path_counts: dict[str, int] = {}
    for entry in entries:
        path_counts[entry["path"]] = path_counts.get(entry["path"], 0) + 1
    for entry in entries:
        if path_counts[entry["path"]] == 1:
            by_key[entry["path"]] = entry
    return by_key


def _atomic_create(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise HTTPException(status_code=400, detail=f"unsafe parent directory: {path.parent}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, mode)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="file appeared concurrently; refresh before saving")
    with os.fdopen(fd, "wb") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())


def _atomic_create_once(path: Path, content: bytes, mode: int) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.bc-sync-", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.link(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _create_backup_once(path: Path, content: bytes) -> None:
    backup = _backup_path(path)
    marker = _backup_marker_path(backup)
    digest = hashlib.sha256(content).hexdigest().encode("ascii")
    mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
    if backup.exists() or marker.exists() or backup.is_symlink() or marker.is_symlink():
        if (
            not backup.is_file()
            or backup.is_symlink()
            or not marker.is_file()
            or marker.is_symlink()
        ):
            raise HTTPException(status_code=500, detail=f"backup is incomplete or unsafe: {backup}")
        if hashlib.sha256(backup.read_bytes()).hexdigest().encode("ascii") != marker.read_bytes():
            raise HTTPException(status_code=500, detail=f"backup integrity check failed: {backup}")
        return
    try:
        _atomic_create_once(backup, content, mode)
        _atomic_create_once(marker, digest, mode)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="backup appeared concurrently; refresh before saving")


def _write_if_unchanged(path: Path, expected: str | None, content: str, category: str) -> None:
    encoded = content.encode("utf-8")
    if expected is None:
        _atomic_create(path, encoded, _file_mode(category))
        return

    real = _real_existing_file(path)
    if real is None:
        raise HTTPException(status_code=409, detail="file disappeared; refresh before saving")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(real, flags)
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"file changed or became unsafe: {e}")
    expected_bytes = expected.encode("utf-8")
    with os.fdopen(fd, "r+b") as fh:
        opened_stat = os.fstat(fh.fileno())
        try:
            path_stat = real.stat(follow_symlinks=False)
        except OSError as e:
            raise HTTPException(status_code=409, detail=f"file changed: {e}")
        current = fh.read()
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or opened_stat.st_dev != path_stat.st_dev
            or opened_stat.st_ino != path_stat.st_ino
            or opened_stat.st_nlink != 1
            or current != expected_bytes
        ):
            raise HTTPException(status_code=409, detail="file changed; refresh before saving")
        _create_backup_once(real, current)
        fh.seek(0)
        fh.write(encoded)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())


_BARE_TOML_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(key: str) -> str:
    if _BARE_TOML_KEY_RE.match(key):
        return key
    return json.dumps(key)


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HTTPException(status_code=400, detail="TOML cannot contain non-finite numbers")
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(f"{_toml_key(str(key))} = {_toml_value(item)}" for key, item in value.items())
        return "{ " + items + " }"
    raise HTTPException(status_code=400, detail=f"unsupported TOML value type: {type(value).__name__}")


def _dump_toml_table(lines: list[str], path: list[str], table: dict) -> None:
    for key, value in table.items():
        if isinstance(value, dict):
            continue
        lines.append(f"{_toml_key(str(key))} = {_toml_value(value)}")
    for key, value in table.items():
        if not isinstance(value, dict):
            continue
        if lines and lines[-1] != "":
            lines.append("")
        next_path = [*path, str(key)]
        lines.append("[" + ".".join(_toml_key(part) for part in next_path) + "]")
        _dump_toml_table(lines, next_path, value)


def _toml_dumps(data: dict) -> str:
    lines: list[str] = []
    _dump_toml_table(lines, [], data)
    return "\n".join(lines).rstrip() + "\n"


def _read_entry_current(entry: dict) -> tuple[str, bool]:
    path = Path(entry["path"])
    content_kind = entry.get("content_kind") or _CONTENT_FILE
    return _content_adapter(content_kind).read_current(path)


def _expected_content(content: str, exists: bool) -> str | None:
    return content if exists else None


def _write_json_mcp_if_unchanged(path: Path, expected: str | None, content: str, category: str) -> None:
    original = _read_existing_text(path)
    data = _json_object_from_text(path, original) if original is not None else {}
    current = _mcp_fragment_from_servers(path, data["mcpServers"]) if "mcpServers" in data else None
    if current != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before saving")
    data["mcpServers"] = _mcp_servers_from_fragment(content)
    _write_if_unchanged(path, original, json.dumps(data, indent=2, sort_keys=True) + "\n", category)


def _write_toml_mcp_if_unchanged(path: Path, expected: str | None, content: str, category: str) -> None:
    original = _read_existing_text(path)
    data = _toml_object_from_text(path, original) if original is not None else {}
    current = _mcp_fragment_from_servers(path, data["mcp_servers"]) if "mcp_servers" in data else None
    if current != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before saving")
    data["mcp_servers"] = _mcp_servers_from_fragment(content)
    _write_if_unchanged(path, original, _toml_dumps(data), category)


def _markdown_agent_from_normalized(content: str) -> str:
    payload = _item_payload_from_normalized(content, "agent")
    metadata = payload.get("metadata") or {}
    frontmatter = {
        "name": payload["name"],
        "description": payload["description"],
        **metadata,
    }
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body = payload["instructions"].rstrip() + "\n"
    return f"---\n{yaml_text}\n---\n{body}"


def _markdown_skill_from_normalized(content: str) -> str:
    payload = _item_payload_from_normalized(content, "skill")
    metadata = payload.get("metadata") or {}
    frontmatter = {
        "name": payload["name"],
        "description": payload["description"],
        **metadata,
    }
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body = payload["instructions"].rstrip() + "\n"
    return f"---\n{yaml_text}\n---\n{body}"


def _toml_agent_from_normalized(content: str) -> str:
    payload = _item_payload_from_normalized(content, "agent")
    data = {
        "name": payload["name"],
        "description": payload["description"],
        "developer_instructions": payload["instructions"].rstrip() + "\n",
        **(payload.get("metadata") or {}),
    }
    return _toml_dumps(data)


def _markdown_command_from_normalized(content: str) -> str:
    payload = _item_payload_from_normalized(content, "command")
    metadata = payload.get("metadata") or {}
    frontmatter = {
        **({"description": payload["description"]} if payload.get("description") else {}),
        **metadata,
    }
    body = payload["instructions"].rstrip() + "\n"
    if not frontmatter:
        return body
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{yaml_text}\n---\n{body}"


def _toml_command_from_normalized(content: str) -> str:
    payload = _item_payload_from_normalized(content, "command")
    data = {
        **({"description": payload["description"]} if payload.get("description") else {}),
        "prompt": payload["instructions"].rstrip() + "\n",
        **(payload.get("metadata") or {}),
    }
    return _toml_dumps(data)


def _codex_command_skill_from_normalized(content: str) -> str:
    payload = _item_payload_from_normalized(content, "command")
    metadata = {
        _CODEX_COMMAND_SKILL_KIND_KEY: "command",
        _CODEX_COMMAND_SKILL_NAME_KEY: payload["name"],
        _CODEX_COMMAND_SKILL_DESCRIPTION_KEY: payload["description"],
        **(payload.get("metadata") or {}),
    }
    frontmatter = {
        "name": _codex_command_skill_name(payload["name"]),
        "description": payload["description"] or f"Run the {payload['name']} command workflow",
        **metadata,
    }
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body = payload["instructions"].rstrip() + "\n"
    return f"---\n{yaml_text}\n---\n{body}"


def _write_markdown_agent_if_unchanged(path: Path, expected: str | None, content: str, category: str) -> None:
    original = _read_existing_text(path)
    current = _normalized_item_text(_markdown_agent_payload(path, original)) if original is not None else None
    if current != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before saving")
    _write_if_unchanged(path, original, _markdown_agent_from_normalized(content), category)


def _write_markdown_skill_if_unchanged(path: Path, expected: str | None, content: str, category: str) -> None:
    original = _read_existing_text(path)
    current = _normalized_item_text(_markdown_skill_payload(path, original)) if original is not None else None
    if current != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before saving")
    _write_if_unchanged(path, original, _markdown_skill_from_normalized(content), category)


def _write_toml_agent_if_unchanged(path: Path, expected: str | None, content: str, category: str) -> None:
    original = _read_existing_text(path)
    current = _normalized_item_text(_toml_agent_payload(path, original)) if original is not None else None
    if current != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before saving")
    _write_if_unchanged(path, original, _toml_agent_from_normalized(content), category)


def _write_markdown_command_if_unchanged(path: Path, expected: str | None, content: str, category: str) -> None:
    original = _read_existing_text(path)
    current = _normalized_item_text(_markdown_command_payload(path, original)) if original is not None else None
    if current != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before saving")
    _write_if_unchanged(path, original, _markdown_command_from_normalized(content), category)


def _write_toml_command_if_unchanged(path: Path, expected: str | None, content: str, category: str) -> None:
    original = _read_existing_text(path)
    current = _normalized_item_text(_toml_command_payload(path, original)) if original is not None else None
    if current != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before saving")
    _write_if_unchanged(path, original, _toml_command_from_normalized(content), category)


def _write_codex_command_skill_if_unchanged(path: Path, expected: str | None, content: str, category: str) -> None:
    original = _read_existing_text(path)
    current = _normalized_item_text(_codex_command_skill_payload(path, original)) if original is not None else None
    if current != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before saving")
    _write_if_unchanged(path, original, _codex_command_skill_from_normalized(content), category)


def _write_file_if_unchanged(path: Path, expected: str | None, content: str, category: str) -> None:
    _write_if_unchanged(path, expected, content, category)


def _content_adapter(content_kind: str) -> ContentAdapter:
    adapters = {
        _CONTENT_FILE: ContentAdapter(_file_current, _write_file_if_unchanged),
        _CONTENT_JSON_MCP: ContentAdapter(_json_mcp_current, _write_json_mcp_if_unchanged),
        _CONTENT_TOML_MCP: ContentAdapter(_toml_mcp_current, _write_toml_mcp_if_unchanged),
        _CONTENT_MARKDOWN_SKILL: ContentAdapter(_markdown_skill_current, _write_markdown_skill_if_unchanged),
        _CONTENT_MARKDOWN_AGENT: ContentAdapter(_markdown_agent_current, _write_markdown_agent_if_unchanged),
        _CONTENT_TOML_AGENT: ContentAdapter(_toml_agent_current, _write_toml_agent_if_unchanged),
        _CONTENT_MARKDOWN_COMMAND: ContentAdapter(_markdown_command_current, _write_markdown_command_if_unchanged),
        _CONTENT_TOML_COMMAND: ContentAdapter(_toml_command_current, _write_toml_command_if_unchanged),
        _CONTENT_CODEX_COMMAND_SKILL: ContentAdapter(_codex_command_skill_current, _write_codex_command_skill_if_unchanged),
    }
    adapter = adapters.get(content_kind)
    if adapter is None:
        raise HTTPException(status_code=400, detail=f"unsupported content kind: {content_kind}")
    return adapter


def _write_entry_if_unchanged(entry: dict, expected: str | None, content: str) -> None:
    path = Path(entry["path"])
    content_kind = entry.get("content_kind") or _CONTENT_FILE
    _content_adapter(content_kind).write_if_unchanged(path, expected, content, entry["category"])


def _restore_entry_backup_if_unchanged(entry: dict, expected: str | None) -> None:
    current, exists = _read_entry_current(entry)
    if _expected_content(current, exists) != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before restoring")
    real = _real_existing_file(Path(entry["path"]))
    if real is None:
        raise HTTPException(status_code=409, detail="file disappeared; refresh before restoring")
    backup = _backup_path(real)
    marker = _backup_marker_path(backup)
    if not _backup_exists(real):
        raise HTTPException(status_code=404, detail="backup does not exist")
    backup_content = backup.read_bytes()
    if hashlib.sha256(backup_content).hexdigest().encode("ascii") != marker.read_bytes():
        raise HTTPException(status_code=500, detail=f"backup integrity check failed: {backup}")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(real, flags)
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"file changed or became unsafe: {e}")
    with os.fdopen(fd, "r+b") as fh:
        opened_stat = os.fstat(fh.fileno())
        try:
            path_stat = real.stat(follow_symlinks=False)
        except OSError as e:
            raise HTTPException(status_code=409, detail=f"file changed: {e}")
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or opened_stat.st_dev != path_stat.st_dev
            or opened_stat.st_ino != path_stat.st_ino
            or opened_stat.st_nlink != 1
        ):
            raise HTTPException(status_code=409, detail="file changed; refresh before restoring")
        fh.seek(0)
        fh.write(backup_content)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())


async def _broadcast_changed(scope: str, category: str, capability_id: str, path: str, cwd: str) -> None:
    result = _broadcast_changed_source(scope, category, capability_id, path, cwd)
    if hasattr(result, "__await__"):
        await result


@router.get("")
async def get_provider_sync(cwd: str = Query("", description="Project cwd for project-scope native files")):
    return _discover(cwd)


@router.get("/projects")
async def list_provider_sync_projects():
    projects = []
    for project in _project_records_source():
        path = project.get("path")
        if not isinstance(path, str) or not path:
            continue
        projects.append(
            {
                **project,
                "path": str(_expand_path(path).resolve()),
                "name": project.get("name") or Path(path).name or path,
                "node_id": project.get("node_id") or "primary",
            }
        )
    return {"projects": projects}


def _picker_source_id(scope: str, source_cwd: str, category: str, capability_id: str) -> str:
    payload = "\0".join([scope, source_cwd, category, capability_id])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _picker_preferred_entry(capability: dict) -> dict | None:
    unified = capability.get("unified")
    if isinstance(unified, dict) and unified.get("exists"):
        return unified
    for entry in capability.get("specifics") or []:
        if isinstance(entry, dict) and entry.get("exists"):
            return entry
    return None


def _picker_project_cwds(cwd: str = "") -> list[str]:
    seen: dict[str, None] = {}
    for project in _project_records_source():
        if (project.get("node_id") or "primary") != "primary":
            continue
        path = project.get("path")
        if isinstance(path, str) and path:
            seen[str(_expand_path(path).resolve())] = None
    if cwd:
        try:
            seen[str(_local_project_root(cwd))] = None
        except HTTPException:
            pass
    return list(seen)


def _capability_picker_sources(cwd: str = "") -> list[dict]:
    sources: list[dict] = []
    global_payload = _discover("")
    for capability in global_payload["groups"]["global"]:
        source_cwd = ""
        sources.append(
            {
                "source_id": _picker_source_id("global", source_cwd, capability["category"], capability["capability_id"]),
                "source_scope": "global",
                "source_cwd": source_cwd,
                "source_label": "Global",
                "capability": capability,
                "preferred_entry": _picker_preferred_entry(capability),
            }
        )
    for source_cwd in _picker_project_cwds(cwd):
        payload = _discover(source_cwd)
        for capability in payload["groups"]["project"]:
            sources.append(
                {
                    "source_id": _picker_source_id("project", source_cwd, capability["category"], capability["capability_id"]),
                    "source_scope": "project",
                    "source_cwd": source_cwd,
                    "source_label": Path(source_cwd).name or source_cwd,
                    "capability": capability,
                    "preferred_entry": _picker_preferred_entry(capability),
                }
            )
    return sources


def list_capability_picker_sources(cwd: str = "") -> dict:
    return {"sources": _capability_picker_sources(cwd)}


@router.get("/capability-picker")
async def capability_picker_route(cwd: str = Query("", description="Optional current cwd for picker context")):
    return list_capability_picker_sources(cwd)


class WriteNativeFileRequest(BaseModel):
    cwd: str = ""
    entry_id: str | None = None
    path: str | None = None
    expected_content: str | None = None
    content: str


class RestoreNativeFileRequest(BaseModel):
    cwd: str = ""
    entry_id: str | None = None
    path: str | None = None
    expected_content: str | None = None


class DeleteCapabilityRequest(BaseModel):
    cwd: str = ""
    scope: str | None = None
    capability_id: str
    expected_contents: dict[str, str | None]


class CreateCapabilityRequest(BaseModel):
    cwd: str = ""
    scope: str
    category: str
    provider_kinds: list[str]
    name: str
    description: str = ""
    instructions: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class TransferCapabilityRequest(BaseModel):
    cwd: str = ""
    scope: str
    capability_id: str
    target_cwd: str = ""
    target_scope: str
    mode: str
    expected_contents: dict[str, str | None]


def _providers_by_kinds(provider_kinds: list[str]) -> list[dict]:
    requested = [kind for kind in dict.fromkeys(provider_kinds) if kind]
    if not requested:
        raise HTTPException(status_code=400, detail="at least one provider is required")
    providers = {provider["kind"]: provider for provider in _provider_records()}
    missing = [kind for kind in requested if kind not in providers]
    if missing:
        raise HTTPException(status_code=400, detail=f"unknown provider kind: {', '.join(missing)}")
    return [providers[kind] for kind in requested]


def _project_root_for_scope(scope: str, cwd: str) -> Path | None:
    if scope == "global":
        return None
    if scope == "project":
        return _local_project_root(cwd)
    raise HTTPException(status_code=400, detail="scope must be global or project")


def _new_capability_candidate(req: CreateCapabilityRequest, provider: dict, project_root: Path | None) -> Candidate:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="capability name is required")
    if req.category == "agent":
        candidates = _agent_candidates(provider, req.scope, {name}, project_root)
    elif req.category == "command":
        candidates = _command_candidates(provider, req.scope, {name}, project_root)
    elif req.category == "skill":
        if req.scope == "global":
            candidates = _global_skill_candidates(provider, {name})
        elif project_root is not None:
            candidates = _project_skill_candidates(provider, project_root, req.cwd, {(".", name)})
        else:
            candidates = []
    else:
        raise HTTPException(status_code=400, detail="category must be agent, command, or skill")
    if not candidates:
        raise HTTPException(status_code=400, detail="provider does not support creating that capability")
    return candidates[0]


def _new_capability_content(req: CreateCapabilityRequest) -> str:
    item = {
        "name": req.name,
        "description": req.description,
        "instructions": req.instructions,
        "metadata": req.metadata or {},
    }
    return _normalized_common_item_from_tool({"category": req.category}, item, req.name)


def _common_item_name_from_capability(capability: dict) -> str:
    source = capability["unified"] if capability["unified"]["exists"] else None
    if source is None:
        source = next((entry for entry in capability["specifics"] if entry["exists"]), None)
    if source is None:
        raise HTTPException(status_code=400, detail="source capability has no content to transfer")
    try:
        parsed = json.loads(source["content"])
    except json.JSONDecodeError:
        parsed = {}
    name = parsed.get("name") if isinstance(parsed, dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()
    capability_id = capability["capability_id"]
    for prefix in (_SKILL_CAPABILITY_PREFIX, _AGENT_CAPABILITY_PREFIX, _COMMAND_CAPABILITY_PREFIX):
        if capability_id.startswith(prefix):
            return capability_id.removeprefix(prefix)
    raise HTTPException(status_code=400, detail="could not resolve capability transfer name")


def _merged_entries_from_candidates(candidates: list[Candidate]) -> list[dict]:
    by_entry: dict[str, dict] = {}
    for candidate in candidates:
        _merge_entry(_entry_from_candidate(candidate), by_entry)
    return list(by_entry.values())


@router.put("/file")
async def write_native_file(req: WriteNativeFileRequest):
    entries = _entry_map(req.cwd)
    entry_key = req.entry_id or req.path
    if entry_key is None:
        raise HTTPException(status_code=400, detail="entry_id is required")
    entry = entries.get(entry_key)
    if entry is None or not entry.get("writable"):
        raise HTTPException(status_code=400, detail="entry is not an editable sync file")
    with _lock:
        current, exists = _read_entry_current(entry)
        if _expected_content(current, exists) != req.expected_content:
            raise HTTPException(status_code=409, detail="file changed; refresh before saving")
        _write_entry_if_unchanged(entry, req.expected_content, req.content)
    await _broadcast_changed(entry["scope"], entry["category"], entry["capability_id"], entry["path"], req.cwd)
    return {"ok": True, "path": entry["path"]}


def _delete_file_if_unchanged(path: Path, expected: str | None, current: str) -> bool:
    if expected is None:
        return False
    if path.is_symlink():
        raise HTTPException(status_code=400, detail=f"refusing to delete symlinked path: {path}")
    real = _real_existing_file(path)
    if real is None:
        raise HTTPException(status_code=409, detail="file disappeared; refresh before deleting")
    if real != path.resolve(strict=True):
        raise HTTPException(status_code=400, detail=f"refusing to delete indirect path: {path}")
    if current != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before deleting")
    _create_backup_once(real, current.encode("utf-8"))
    try:
        real.unlink()
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"file changed or could not be deleted: {e}")
    return True


def _delete_json_mcp_if_unchanged(path: Path, expected: str | None) -> bool:
    if expected is None:
        return False
    original = _read_existing_text(path)
    data = _json_object_from_text(path, original) if original is not None else {}
    if "mcpServers" not in data:
        raise HTTPException(status_code=409, detail="file changed; refresh before deleting")
    current = _mcp_fragment_from_servers(path, data["mcpServers"])
    if current != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before deleting")
    data.pop("mcpServers", None)
    _write_if_unchanged(path, original, json.dumps(data, indent=2, sort_keys=True) + "\n", "config")
    return True


def _delete_toml_mcp_if_unchanged(path: Path, expected: str | None) -> bool:
    if expected is None:
        return False
    original = _read_existing_text(path)
    data = _toml_object_from_text(path, original) if original is not None else {}
    if "mcp_servers" not in data:
        raise HTTPException(status_code=409, detail="file changed; refresh before deleting")
    current = _mcp_fragment_from_servers(path, data["mcp_servers"])
    if current != expected:
        raise HTTPException(status_code=409, detail="file changed; refresh before deleting")
    data.pop("mcp_servers", None)
    _write_if_unchanged(path, original, _toml_dumps(data), "config")
    return True


def _delete_entry_if_unchanged(entry: dict, expected: str | None) -> bool:
    current, exists = _read_entry_current(entry)
    if not exists:
        if expected is not None:
            raise HTTPException(status_code=409, detail="file disappeared; refresh before deleting")
        return False
    content_kind = entry.get("content_kind") or _CONTENT_FILE
    path = Path(entry["path"])
    if content_kind == _CONTENT_JSON_MCP:
        return _delete_json_mcp_if_unchanged(path, expected)
    if content_kind == _CONTENT_TOML_MCP:
        return _delete_toml_mcp_if_unchanged(path, expected)
    return _delete_file_if_unchanged(path, expected, current)


async def delete_capability(req: DeleteCapabilityRequest):
    capability = _capability_for_tool(req.cwd, req.capability_id, req.scope)
    entries = [capability["unified"], *capability["specifics"]]
    deleted_paths: list[str] = []
    with _lock:
        latest = _capability_for_tool(req.cwd, req.capability_id, req.scope)
        latest_entries = [latest["unified"], *latest["specifics"]]
        for entry in latest_entries:
            if entry["entry_id"] not in req.expected_contents:
                raise HTTPException(status_code=400, detail="expected content missing for capability entry")
            expected = req.expected_contents[entry["entry_id"]]
            current, exists = _read_entry_current(entry)
            if _expected_content(current, exists) != expected:
                raise HTTPException(status_code=409, detail="file changed; refresh before deleting")
        for entry in latest_entries:
            if _delete_entry_if_unchanged(entry, req.expected_contents[entry["entry_id"]]):
                deleted_paths.append(entry["path"])
    for entry in entries:
        if entry["path"] in deleted_paths:
            await _broadcast_changed(entry["scope"], entry["category"], entry["capability_id"], entry["path"], req.cwd)
    return {"ok": True, "capability_id": capability["capability_id"], "deleted_paths": deleted_paths}


@router.delete("/capability")
async def delete_capability_route(req: DeleteCapabilityRequest):
    return await delete_capability(req)


async def create_capability(req: CreateCapabilityRequest):
    providers = _providers_by_kinds(req.provider_kinds)
    project_root = _project_root_for_scope(req.scope, req.cwd)
    candidates = [_new_capability_candidate(req, provider, project_root) for provider in providers]
    entries = _merged_entries_from_candidates(candidates)
    for entry in entries:
        if entry["exists"]:
            raise HTTPException(status_code=409, detail=f"capability already exists for {entry['provider_names'][0]}")
        if not entry["writable"]:
            raise HTTPException(status_code=400, detail=f"capability is not writable for {entry['provider_names'][0]}")
    first_entry = entries[0]
    unified = _unified_entry(
        scope=req.scope,
        category=req.category,
        capability_id=first_entry["capability_id"],
        capability_name=first_entry["capability_name"],
        language=first_entry["language"],
        project_root=project_root,
    )
    if unified["exists"]:
        raise HTTPException(status_code=409, detail="unified capability already exists")
    if not unified["writable"]:
        raise HTTPException(status_code=400, detail="unified capability is not writable")
    content = _new_capability_content(req)
    with _lock:
        latest_unified_current, latest_unified_exists = _read_entry_current(unified)
        if latest_unified_exists:
            raise HTTPException(status_code=409, detail="unified capability appeared concurrently; refresh before creating")
        latest_entries = _merged_entries_from_candidates(candidates)
        for latest in latest_entries:
            if latest["exists"]:
                raise HTTPException(status_code=409, detail="capability appeared concurrently; refresh before creating")
        _write_entry_if_unchanged(unified, latest_unified_current if latest_unified_exists else None, content)
        for latest in latest_entries:
            _write_entry_if_unchanged(latest, None, content)
    changed_entries = [unified, *entries]
    for entry in changed_entries:
        await _broadcast_changed(entry["scope"], entry["category"], entry["capability_id"], entry["path"], req.cwd)
    capability = _capability_for_tool(req.cwd, first_entry["capability_id"], req.scope)
    return {"ok": True, "paths": [entry["path"] for entry in changed_entries], "capability": capability}


@router.post("/capability")
async def create_capability_route(req: CreateCapabilityRequest):
    return await create_capability(req)


def _transfer_capability_targets(req: TransferCapabilityRequest, source: dict) -> tuple[dict, list[dict]]:
    if source["category"] not in {"skill", "agent", "command"}:
        raise HTTPException(status_code=400, detail="move/copy supports skill, agent, and command capabilities")
    providers = _provider_records()
    target_project_root = _project_root_for_scope(req.target_scope, req.target_cwd)
    create_req = CreateCapabilityRequest(
        cwd=req.target_cwd,
        scope=req.target_scope,
        category=source["category"],
        provider_kinds=[provider["kind"] for provider in providers],
        name=_common_item_name_from_capability(source),
    )
    candidates = [_new_capability_candidate(create_req, provider, target_project_root) for provider in providers]
    entries = _merged_entries_from_candidates(candidates)
    first_entry = entries[0]
    unified = _unified_entry(
        scope=req.target_scope,
        category=source["category"],
        capability_id=first_entry["capability_id"],
        capability_name=first_entry["capability_name"],
        language=first_entry["language"],
        project_root=target_project_root,
    )
    return unified, entries


async def transfer_capability(req: TransferCapabilityRequest):
    if req.mode not in {"copy", "move"}:
        raise HTTPException(status_code=400, detail="mode must be copy or move")
    if req.scope == req.target_scope and req.cwd == req.target_cwd:
        raise HTTPException(status_code=400, detail="target must be a different level or project")
    source = _capability_for_tool(req.cwd, req.capability_id, req.scope)
    source_entries = [source["unified"], *source["specifics"]]
    target_unified, target_entries = _transfer_capability_targets(req, source)
    changed_entries = [target_unified, *target_entries]
    deleted_paths: list[str] = []
    with _lock:
        latest_source = _capability_for_tool(req.cwd, req.capability_id, req.scope)
        latest_source_entries = [latest_source["unified"], *latest_source["specifics"]]
        for entry in latest_source_entries:
            if entry["entry_id"] not in req.expected_contents:
                raise HTTPException(status_code=400, detail="expected content missing for capability entry")
            current, exists = _read_entry_current(entry)
            if _expected_content(current, exists) != req.expected_contents[entry["entry_id"]]:
                raise HTTPException(status_code=409, detail="file changed; refresh before moving or copying")
        latest_target_unified, latest_target_entries = _transfer_capability_targets(req, latest_source)
        for entry in [latest_target_unified, *latest_target_entries]:
            if entry["exists"]:
                raise HTTPException(status_code=409, detail="target capability already exists")
            if not entry["writable"]:
                raise HTTPException(status_code=400, detail="target capability is not writable")
        source_by_provider = {
            entry["provider_kinds"][0]: entry
            for entry in latest_source_entries
            if entry["exists"] and entry["provider_kinds"]
        }
        unified_source = latest_source["unified"] if latest_source["unified"]["exists"] else None
        fallback_source = unified_source or next((entry for entry in latest_source["specifics"] if entry["exists"]), None)
        if fallback_source is None:
            raise HTTPException(status_code=400, detail="source capability has no content to transfer")
        _write_entry_if_unchanged(latest_target_unified, None, fallback_source["content"])
        for target in latest_target_entries:
            source_entry = source_by_provider.get(target["provider_kinds"][0], fallback_source)
            _write_entry_if_unchanged(target, None, source_entry["content"])
        if req.mode == "move":
            for entry in latest_source_entries:
                if _delete_entry_if_unchanged(entry, req.expected_contents[entry["entry_id"]]):
                    deleted_paths.append(entry["path"])
    for entry in changed_entries:
        await _broadcast_changed(entry["scope"], entry["category"], entry["capability_id"], entry["path"], req.target_cwd)
    for entry in source_entries:
        if entry["path"] in deleted_paths:
            await _broadcast_changed(entry["scope"], entry["category"], entry["capability_id"], entry["path"], req.cwd)
    target = _capability_for_tool(req.target_cwd, target_unified["capability_id"], req.target_scope)
    return {"ok": True, "mode": req.mode, "capability": target, "deleted_paths": deleted_paths}


@router.post("/capability/transfer")
async def transfer_capability_route(req: TransferCapabilityRequest):
    return await transfer_capability(req)


@router.post("/file/restore")
async def restore_native_file(req: RestoreNativeFileRequest):
    entries = _entry_map(req.cwd)
    entry_key = req.entry_id or req.path
    if entry_key is None:
        raise HTTPException(status_code=400, detail="entry_id is required")
    entry = entries.get(entry_key)
    if entry is None or not entry.get("writable"):
        raise HTTPException(status_code=400, detail="entry is not an editable sync file")
    with _lock:
        _restore_entry_backup_if_unchanged(entry, req.expected_content)
    await _broadcast_changed(entry["scope"], entry["category"], entry["capability_id"], entry["path"], req.cwd)
    return {"ok": True, "path": entry["path"]}


class ApplyNativeFileRequest(BaseModel):
    cwd: str = ""
    capability_id: str
    source_entry_id: str | None = None
    target_entry_id: str | None = None
    source_path: str | None = None
    target_path: str | None = None
    expected_source: str
    expected_target: str | None = None


class AutoSyncPolicy(BaseModel):
    additive: str = "off"
    removal: str = "off"
    change: str = "off"


class AutoSyncSettingsPatch(BaseModel):
    level: str
    policy: dict[str, Any]
    cwd: str = ""
    capability_id: str = ""


class AutoSyncRequest(BaseModel):
    cwd: str = ""
    capability_id: str
    source_entry_id: str
    target_entry_id: str
    expected_source: str
    expected_target: str | None = None
    policy: AutoSyncPolicy
    approved_hunk_ids: list[str] = []
    llm_hunk_ids: list[str] = []


class UpsertUnifiedCapabilityItemRequest(BaseModel):
    cwd: str = ""
    scope: str | None = None
    capability_id: str
    item_name: str | None = None
    item: dict[str, Any]
    expected_content: str | None = None


class RemoveUnifiedCapabilityItemRequest(BaseModel):
    cwd: str = ""
    scope: str | None = None
    capability_id: str
    item_name: str
    expected_content: str | None = None


def _capability_for_tool(cwd: str, capability_id: str, scope: str | None = None) -> dict:
    preferred_scope = scope or ("project" if cwd else "global")
    matches = [capability for capability in _discover(cwd)["capabilities"] if capability["capability_id"] == capability_id]
    for capability in matches:
        if capability["scope"] == preferred_scope:
            return capability
    for capability in matches:
        if scope is None:
            return capability
    raise HTTPException(status_code=400, detail=f"unknown provider config sync capability: {capability_id}")


def _current_unified_for_tool(cwd: str, capability_id: str, scope: str | None = None) -> tuple[dict, str, bool]:
    capability = _capability_for_tool(cwd, capability_id, scope)
    entry = capability["unified"]
    if not entry.get("writable"):
        raise HTTPException(status_code=400, detail="unified entry is not writable")
    current, exists = _read_entry_current(entry)
    return capability, current, exists


def _check_tool_expected(current: str, exists: bool, expected: str | None) -> None:
    if expected is None:
        return
    if _expected_content(current, exists) != expected:
        raise HTTPException(status_code=409, detail="unified file changed; refresh before editing")


def _normalized_common_item_from_tool(capability: dict, item: dict[str, Any], item_name: str | None) -> str:
    metadata = item.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="metadata must be an object")
    if capability["category"] == "command":
        payload = _normalized_command_payload(
            path=Path("<command>"),
            name=item.get("name") or item_name,
            description=item.get("description"),
            instructions=item.get("instructions"),
            metadata=metadata,
        )
        return _normalized_item_text(payload)
    payload = _normalized_item_payload(
        item_label=capability["category"],
        path=Path(f"<{capability['category']}>"),
        name=item.get("name") or item_name,
        description=item.get("description"),
        instructions=item.get("instructions"),
        metadata=metadata,
    )
    return _normalized_item_text(payload)


def _mcp_tool_content(current: str, exists: bool) -> dict[str, Any]:
    if not exists:
        return {"mcpServers": {}}
    return {"mcpServers": _mcp_servers_from_fragment(current)}


async def upsert_unified_capability_item(req: UpsertUnifiedCapabilityItemRequest):
    capability, current, exists = _current_unified_for_tool(req.cwd, req.capability_id, req.scope)
    _check_tool_expected(current, exists, req.expected_content)
    entry = capability["unified"]
    if capability["capability_id"] == _MCP_CAPABILITY_ID:
        name = (req.item_name or req.item.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="item_name is required for MCP server edits")
        item = {key: value for key, value in req.item.items() if key != "name"}
        try:
            json.dumps(item)
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"MCP server item is not JSON-compatible: {e}")
        content = _mcp_tool_content(current, exists)
        content["mcpServers"][name] = item
        next_content = json.dumps(content, indent=2, sort_keys=True) + "\n"
    elif capability["category"] in {"agent", "skill", "command"}:
        next_content = _normalized_common_item_from_tool(capability, req.item, req.item_name)
    else:
        raise HTTPException(status_code=400, detail=f"capability does not support item edits: {capability['category']}")
    with _lock:
        latest, latest_exists = _read_entry_current(entry)
        _check_tool_expected(latest, latest_exists, req.expected_content)
        if req.expected_content is None:
            current, exists = latest, latest_exists
            if capability["capability_id"] == _MCP_CAPABILITY_ID:
                content = _mcp_tool_content(current, exists)
                name = (req.item_name or req.item.get("name") or "").strip()
                item = {key: value for key, value in req.item.items() if key != "name"}
                content["mcpServers"][name] = item
                next_content = json.dumps(content, indent=2, sort_keys=True) + "\n"
        _write_entry_if_unchanged(entry, _expected_content(latest, latest_exists), next_content)
    await _broadcast_changed(entry["scope"], entry["category"], entry["capability_id"], entry["path"], req.cwd)
    return {"ok": True, "path": entry["path"], "capability_id": capability["capability_id"], "content": next_content}


async def remove_unified_capability_item(req: RemoveUnifiedCapabilityItemRequest):
    capability, current, exists = _current_unified_for_tool(req.cwd, req.capability_id, req.scope)
    _check_tool_expected(current, exists, req.expected_content)
    if capability["capability_id"] != _MCP_CAPABILITY_ID:
        raise HTTPException(status_code=400, detail="remove item currently supports MCP server items only")
    name = req.item_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="item_name is required")
    entry = capability["unified"]
    with _lock:
        latest, latest_exists = _read_entry_current(entry)
        _check_tool_expected(latest, latest_exists, req.expected_content)
        content = _mcp_tool_content(latest, latest_exists)
        content["mcpServers"].pop(name, None)
        next_content = json.dumps(content, indent=2, sort_keys=True) + "\n"
        _write_entry_if_unchanged(entry, _expected_content(latest, latest_exists), next_content)
    await _broadcast_changed(entry["scope"], entry["category"], entry["capability_id"], entry["path"], req.cwd)
    return {"ok": True, "path": entry["path"], "capability_id": capability["capability_id"], "content": next_content}


def _split_lines(content: str) -> list[str]:
    lines = re.split(r"\r?\n", content)
    if len(lines) > 1 and lines[-1] == "":
        return lines[:-1]
    return lines


def _join_lines_like(lines: list[str], original: str) -> str:
    return "\n".join(lines) + ("\n" if original.endswith("\n") else "")


def _diff_rows(unified_content: str, specific_content: str) -> list[dict[str, Any]]:
    unified_lines = _split_lines(unified_content)
    specific_lines = _split_lines(specific_content)
    rows: list[dict[str, Any]] = []
    matcher = difflib.SequenceMatcher(a=unified_lines, b=specific_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, text in enumerate(unified_lines[i1:i2]):
                rows.append({
                    "key": f"same:{i1 + offset}:{j1 + offset}",
                    "kind": "same",
                    "unifiedLine": i1 + offset + 1,
                    "specificLine": j1 + offset + 1,
                    "unifiedText": text,
                    "specificText": text,
                })
            continue
        if tag == "replace":
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                rows.append({
                    "key": f"changed:{i1 + offset + 1}:{j1 + offset + 1}",
                    "kind": "changed",
                    "unifiedLine": i1 + offset + 1,
                    "specificLine": j1 + offset + 1,
                    "unifiedText": unified_lines[i1 + offset],
                    "specificText": specific_lines[j1 + offset],
                })
            for offset in range(paired, i2 - i1):
                rows.append({
                    "key": f"removed:{i1 + offset}:{j1 + paired}",
                    "kind": "removed",
                    "unifiedLine": i1 + offset + 1,
                    "specificLine": None,
                    "unifiedText": unified_lines[i1 + offset],
                    "specificText": "",
                })
            for offset in range(paired, j2 - j1):
                rows.append({
                    "key": f"added:{i2}:{j1 + offset}",
                    "kind": "added",
                    "unifiedLine": None,
                    "specificLine": j1 + offset + 1,
                    "unifiedText": "",
                    "specificText": specific_lines[j1 + offset],
                })
            continue
        if tag == "delete":
            for offset, text in enumerate(unified_lines[i1:i2]):
                rows.append({
                    "key": f"removed:{i1 + offset}:{j1}",
                    "kind": "removed",
                    "unifiedLine": i1 + offset + 1,
                    "specificLine": None,
                    "unifiedText": text,
                    "specificText": "",
                })
            continue
        for offset, text in enumerate(specific_lines[j1:j2]):
            rows.append({
                "key": f"added:{i1}:{j1 + offset}",
                "kind": "added",
                "unifiedLine": None,
                "specificLine": j1 + offset + 1,
                "unifiedText": "",
                "specificText": text,
            })
    return rows


def _diff_hunks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for row in rows:
        if row["kind"] == "same":
            if current:
                hunks.append({"key": current[0]["key"], "rows": current})
                current = []
            continue
        current.append(row)
    if current:
        hunks.append({"key": current[0]["key"], "rows": current})
    return hunks


def _insertion_index_for(rows: list[dict[str, Any]], row_index: int, target: str, line_count: int) -> int:
    line_key = "unifiedLine" if target == "unified" else "specificLine"
    for index in range(row_index + 1, len(rows)):
        line_number = rows[index][line_key]
        if line_number is not None:
            return max(0, line_number - 1)
    for index in range(row_index - 1, -1, -1):
        line_number = rows[index][line_key]
        if line_number is not None:
            return min(line_count, line_number)
    return line_count


def _apply_rows_to_content(content: str, rows: list[dict[str, Any]], target: str) -> str:
    lines = _split_lines(content)
    offset = 0
    for row_index, row in enumerate(rows):
        target_line = row["unifiedLine"] if target == "unified" else row["specificLine"]
        source_line = row["specificLine"] if target == "unified" else row["unifiedLine"]
        source_text = row["specificText"] if target == "unified" else row["unifiedText"]
        if target_line is not None and source_line is not None:
            lines[target_line - 1 + offset] = source_text
            continue
        if target_line is None and source_line is not None:
            index = _insertion_index_for(rows, row_index, target, len(lines)) + offset
            lines.insert(min(max(index, 0), len(lines)), source_text)
            offset += 1
            continue
        if target_line is not None and source_line is None:
            del lines[target_line - 1 + offset]
            offset -= 1
    return _join_lines_like(lines, content)


def _target_side(source: dict, target: dict) -> str:
    if source["role"] == "unified" and target["role"] == "specific":
        return "specific"
    if source["role"] == "specific" and target["role"] == "unified":
        return "unified"
    raise HTTPException(status_code=400, detail="sync apply must be between unified and provider-specific entries")


def _operation_for_hunk(rows: list[dict[str, Any]], target: str) -> str:
    operations: set[str] = set()
    for row in rows:
        if row["kind"] == "changed":
            operations.add("change")
            continue
        if target == "specific":
            operations.add("additive" if row["kind"] == "removed" else "removal")
            continue
        operations.add("additive" if row["kind"] == "added" else "removal")
    if len(operations) == 1:
        return operations.pop()
    return "change"


def _policy_mode(policy: AutoSyncPolicy, operation: str) -> str:
    mode = getattr(policy, operation)
    if mode not in {"off", "auto", "review", "llm"}:
        raise HTTPException(status_code=400, detail=f"invalid auto-sync mode for {operation}")
    return mode


def _hunk_preview(rows: list[dict[str, Any]], target: str) -> str:
    source_key = "specificText" if target == "unified" else "unifiedText"
    for row in rows:
        text = row[source_key].strip()
        if text:
            return text[:120]
    return rows[0]["kind"] if rows else "empty hunk"


def _hunk_id(rows: list[dict[str, Any]], operation: str) -> str:
    payload = [
        {
            "kind": row["kind"],
            "unifiedText": row["unifiedText"],
            "specificText": row["specificText"],
        }
        for row in rows
    ]
    digest = hashlib.sha256(json.dumps([operation, payload], sort_keys=True).encode("utf-8")).hexdigest()
    return f"h:{digest[:16]}"


def _auto_sync_plan(
    hunks: list[dict[str, Any]],
    target_side: str,
    policy: AutoSyncPolicy,
    approved_hunk_ids: set[str],
    llm_hunk_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    selected_rows: list[dict[str, Any]] = []
    log: list[dict[str, Any]] = []
    llm_candidates: list[dict[str, Any]] = []
    for hunk in hunks:
        rows = hunk["rows"]
        operation = _operation_for_hunk(rows, target_side)
        hunk_id = _hunk_id(rows, operation)
        mode = _policy_mode(policy, operation)
        status = "skipped"
        if mode == "auto" or hunk_id in approved_hunk_ids:
            selected_rows.extend(rows)
            status = "applied"
        elif mode == "review":
            status = "pending"
        elif mode == "llm" or (llm_hunk_ids is not None and hunk_id in llm_hunk_ids):
            llm_candidates.append({
                "hunk_id": hunk_id,
                "operation": operation,
                "row_count": len(rows),
                "preview": _hunk_preview(rows, target_side),
                "rows": rows,
            })
        item = {
            "hunk_id": hunk_id,
            "operation": operation,
            "mode": mode,
            "status": status,
            "row_count": len(rows),
            "preview": _hunk_preview(rows, target_side),
            "rows": rows,
        }
        log.append(item)
    return selected_rows, log, llm_candidates


def _llm_review_hunk_ids(context: dict[str, Any]) -> set[str]:
    if _llm_review_source is None:
        raise HTTPException(status_code=400, detail="LLM auto-sync review is not configured")
    result = _llm_review_source(context)
    if not isinstance(result, list) or not all(isinstance(item, str) for item in result):
        raise HTTPException(status_code=502, detail="LLM auto-sync review returned invalid hunk ids")
    valid = {item["hunk_id"] for item in context["candidates"]}
    unknown = set(result) - valid
    if unknown:
        raise HTTPException(status_code=502, detail="LLM auto-sync review returned unknown hunk ids")
    return set(result)


@router.post("/apply")
async def apply_native_file(req: ApplyNativeFileRequest):
    entries = _entry_map(req.cwd)
    source_key = req.source_entry_id or req.source_path
    target_key = req.target_entry_id or req.target_path
    if source_key is None or target_key is None:
        raise HTTPException(status_code=400, detail="source_entry_id and target_entry_id are required")
    source = entries.get(source_key)
    target = entries.get(target_key)
    if source is None or not source.get("exists"):
        raise HTTPException(status_code=400, detail="source is not a readable sync entry")
    if target is None or not target.get("writable"):
        raise HTTPException(status_code=400, detail="target is not an editable sync entry")
    if source["capability_key"] != target["capability_key"] or source["capability_id"] != req.capability_id:
        raise HTTPException(status_code=400, detail="source and target must share the same sync capability")
    if source["role"] == target["role"]:
        raise HTTPException(status_code=400, detail="sync apply must be between unified and provider-specific entries")
    if not source.get("exists"):
        raise HTTPException(status_code=400, detail="source is not a readable sync file")
    with _lock:
        source_text, source_exists = _read_entry_current(source)
        target_text, target_exists = _read_entry_current(target)
        if not source_exists:
            raise HTTPException(status_code=400, detail="source is not a readable sync entry")
        if (
            source_text != req.expected_source
            or _expected_content(target_text, target_exists) != req.expected_target
        ):
            raise HTTPException(status_code=409, detail="file changed; refresh before applying")
        _write_entry_if_unchanged(target, req.expected_target, source_text)
    await _broadcast_changed(target["scope"], target["category"], target["capability_id"], target["path"], req.cwd)
    return {"ok": True, "source_path": source["path"], "target_path": target["path"]}


@router.post("/auto-sync")
async def auto_sync(req: AutoSyncRequest):
    entries = _entry_map(req.cwd)
    source = entries.get(req.source_entry_id)
    target = entries.get(req.target_entry_id)
    if source is None or not source.get("exists"):
        raise HTTPException(status_code=400, detail="source is not a readable sync entry")
    if target is None or not target.get("writable"):
        raise HTTPException(status_code=400, detail="target is not an editable sync entry")
    if source["capability_key"] != target["capability_key"] or source["capability_id"] != req.capability_id:
        raise HTTPException(status_code=400, detail="source and target must share the same sync capability")
    target_side = _target_side(source, target)
    approved = set(req.approved_hunk_ids)
    llm_hunk_ids = set(req.llm_hunk_ids)
    with _lock:
        source_text, source_exists = _read_entry_current(source)
        target_text, target_exists = _read_entry_current(target)
        if not source_exists:
            raise HTTPException(status_code=400, detail="source is not a readable sync entry")
        if (
            source_text != req.expected_source
            or _expected_content(target_text, target_exists) != req.expected_target
        ):
            raise HTTPException(status_code=409, detail="file changed; refresh before applying")
        unified_text = source_text if source["role"] == "unified" else target_text
        specific_text = source_text if source["role"] == "specific" else target_text
        hunks = _diff_hunks(_diff_rows(unified_text, specific_text))
        selected_rows, log, llm_candidates = _auto_sync_plan(hunks, target_side, req.policy, approved, llm_hunk_ids)
    if llm_candidates:
        approved.update(_llm_review_hunk_ids({
            "capability_id": req.capability_id,
            "source_label": source["label"],
            "target_label": target["label"],
            "source_role": source["role"],
            "target_role": target["role"],
            "target_side": target_side,
            "candidates": llm_candidates,
        }))
    with _lock:
        source_text, source_exists = _read_entry_current(source)
        target_text, target_exists = _read_entry_current(target)
        if not source_exists:
            raise HTTPException(status_code=400, detail="source is not a readable sync entry")
        if (
            source_text != req.expected_source
            or _expected_content(target_text, target_exists) != req.expected_target
        ):
            raise HTTPException(status_code=409, detail="file changed; refresh before applying")
        unified_text = source_text if source["role"] == "unified" else target_text
        specific_text = source_text if source["role"] == "specific" else target_text
        hunks = _diff_hunks(_diff_rows(unified_text, specific_text))
        selected_rows, log, _llm_candidates = _auto_sync_plan(hunks, target_side, req.policy, approved, llm_hunk_ids)
        next_target_text = _apply_rows_to_content(target_text, selected_rows, target_side) if selected_rows else target_text
        if next_target_text != target_text:
            _write_entry_if_unchanged(target, req.expected_target, next_target_text)
    if next_target_text != target_text:
        await _broadcast_changed(target["scope"], target["category"], target["capability_id"], target["path"], req.cwd)
    return {
        "ok": True,
        "source_entry_id": source["entry_id"],
        "target_entry_id": target["entry_id"],
        "source_path": source["path"],
        "target_path": target["path"],
        "target_side": target_side,
        "applied_count": sum(1 for item in log if item["status"] == "applied"),
        "pending_count": sum(1 for item in log if item["status"] == "pending"),
        "skipped_count": sum(1 for item in log if item["status"] == "skipped"),
        "log_head": log[:8],
        "log": log,
    }


@router.get("/settings")
async def get_auto_sync_settings_route(
    cwd: str = Query("", description="Project cwd for project-scope overrides"),
    capability_id: str = Query("", description="Capability id for effective policy"),
):
    return get_auto_sync_settings(cwd, capability_id)


@router.patch("/settings")
async def update_auto_sync_settings_route(req: AutoSyncSettingsPatch):
    return update_auto_sync_settings(req)


@router.post("/unified-capability-item")
async def upsert_unified_capability_item_route(req: UpsertUnifiedCapabilityItemRequest):
    return await upsert_unified_capability_item(req)


@router.delete("/unified-capability-item")
async def remove_unified_capability_item_route(req: RemoveUnifiedCapabilityItemRequest):
    return await remove_unified_capability_item(req)

"""REST endpoints for editing and syncing provider-native config files."""

from __future__ import annotations

import hashlib
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
from pydantic import BaseModel

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


def configure(
    *,
    provider_records: Callable[[], list[dict]] | None = None,
    project_records: Callable[[], list[dict]] | None = None,
    sync_home: Callable[[], Path] | None = None,
    encode_project_cwd: Callable[[str], str] | None = None,
    broadcast_changed: Callable[[str, str, str, str, str], Any] | None = None,
) -> None:
    global _provider_records_source
    global _project_records_source
    global _sync_home_source
    global _encode_cwd_source
    global _broadcast_changed_source
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
            return [_codex_home(provider) / "prompts"]
        return []
    if project_root is None:
        return []
    if kind == "claude":
        return [project_root / ".claude" / "commands"]
    if kind == "gemini":
        return [project_root / ".gemini" / "commands"]
    return []


def _command_content_kind(provider_kind: str) -> str:
    return _CONTENT_TOML_COMMAND if provider_kind == "gemini" else _CONTENT_MARKDOWN_COMMAND


def _command_suffix(provider_kind: str) -> str:
    return ".toml" if provider_kind == "gemini" else ".md"


def _command_capability_id(name: str) -> str:
    return f"{_COMMAND_CAPABILITY_PREFIX}{_safe_agent_filename(name)}"


def _command_capability_name(name: str) -> str:
    return f"Command/custom prompt: {name}"


def _command_provider_label(provider_name: str, provider_kind: str) -> str:
    if provider_kind == "codex":
        return f"{provider_name} custom prompt"
    return f"{provider_name} command"


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
    suffix = ".toml" if content_kind == _CONTENT_TOML_AGENT else ".md"
    names: set[str] = set()
    for path in root.rglob(f"*{suffix}"):
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
        for path in root.rglob(f"*{suffix}"):
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


def _command_names(providers: list[dict], scope: str, project_root: Path | None = None) -> set[str]:
    names: set[str] = set()
    for provider in providers:
        suffix = _command_suffix(provider["kind"])
        for root in _command_roots_for_provider(provider, scope, project_root):
            names.update(_command_names_in_root(root, suffix))
    return names


def _candidate_command_paths(provider: dict, roots: list[Path], name: str) -> list[Path]:
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


def _capability_from_specifics(
    *,
    scope: str,
    category: str,
    capability_id: str,
    capability_name: str,
    specifics: list[dict],
    project_root: Path | None,
) -> dict:
    language = _capability_language(category, specifics)
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

    capabilities = [
        _capability_from_specifics(
            scope=item["scope"],
            category=item["category"],
            capability_id=item["capability_id"],
            capability_name=item["capability_name"],
            specifics=item["specifics"],
            project_root=project_root if item["scope"] == "project" else None,
        )
        for item in sorted(by_capability.values(), key=lambda item: (item["scope"], item["capability_name"]))
    ]
    files = [capability["unified"] for capability in capabilities]
    for capability in capabilities:
        files.extend(capability["specifics"])
    return {
        "files": files,
        "capabilities": capabilities,
        "token_totals": _token_totals(capabilities),
        "groups": {
            scope: [
                capability
                for capability in capabilities
                if capability["scope"] == scope
            ]
            for scope in ("global", "project")
        },
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
    }
    adapter = adapters.get(content_kind)
    if adapter is None:
        raise HTTPException(status_code=400, detail=f"unsupported content kind: {content_kind}")
    return adapter


def _write_entry_if_unchanged(entry: dict, expected: str | None, content: str) -> None:
    path = Path(entry["path"])
    content_kind = entry.get("content_kind") or _CONTENT_FILE
    _content_adapter(content_kind).write_if_unchanged(path, expected, content, entry["category"])


async def _broadcast_changed(scope: str, category: str, capability_id: str, path: str, cwd: str) -> None:
    result = _broadcast_changed_source(scope, category, capability_id, path, cwd)
    if hasattr(result, "__await__"):
        await result


@router.get("")
async def get_provider_sync(cwd: str = Query("", description="Project cwd for project-scope native files")):
    return _discover(cwd)


class WriteNativeFileRequest(BaseModel):
    cwd: str = ""
    entry_id: str | None = None
    path: str | None = None
    expected_content: str | None = None
    content: str


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


class ApplyNativeFileRequest(BaseModel):
    cwd: str = ""
    capability_id: str
    source_entry_id: str | None = None
    target_entry_id: str | None = None
    source_path: str | None = None
    target_path: str | None = None
    expected_source: str
    expected_target: str | None = None


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


@router.post("/unified-capability-item")
async def upsert_unified_capability_item_route(req: UpsertUnifiedCapabilityItemRequest):
    return await upsert_unified_capability_item(req)


@router.delete("/unified-capability-item")
async def remove_unified_capability_item_route(req: RemoveUnifiedCapabilityItemRequest):
    return await remove_unified_capability_item(req)

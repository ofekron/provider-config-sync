from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .standalone import _read_config

_SERVER_NAME = "provider_config_sync"
_SYNC_TOOLS = [
    "list_provider_config_capabilities",
    "read_provider_config_entry",
    "write_provider_config_entry",
    "apply_provider_config_entry",
    "upsert_unified_capability_item",
    "remove_unified_capability_item",
]


def _expand(raw: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(raw)))).absolute()


def _config_path(raw: str | None) -> Path | None:
    value = raw or os.environ.get("PROVIDER_CONFIG_SYNC_CONFIG")
    return _expand(value) if value else None


def _mcp_command() -> tuple[str, list[str]]:
    installed = shutil.which("provider-config-sync-mcp")
    if installed:
        return installed, []
    return sys.executable, ["-m", "provider_config_sync_backend.mcp_server"]


def _mcp_env(config_path: Path | None) -> dict[str, str]:
    env: dict[str, str] = {}
    if config_path is not None:
        env["PROVIDER_CONFIG_SYNC_CONFIG"] = str(config_path)
    home = os.environ.get("PROVIDER_CONFIG_SYNC_HOME")
    if home:
        env["PROVIDER_CONFIG_SYNC_HOME"] = home
    return env


def _projects(config_path: Path | None) -> list[str]:
    config = _read_config(config_path)
    projects = config.get("projects") or []
    if not isinstance(projects, list):
        raise ValueError("projects must be a list")
    paths: list[str] = []
    for item in projects:
        if not isinstance(item, dict):
            raise ValueError("projects must contain objects")
        path = item.get("path")
        if isinstance(path, str) and path.strip():
            paths.append(str(_expand(path)))
    return sorted(set(paths))


def _automation_prompt(projects: list[str], user_prompt: str) -> str:
    scopes = ['global config: call list_provider_config_capabilities with cwd=""']
    scopes.extend(f"project config: {project}" for project in projects)
    extra = f"\n\nUser request:\n{user_prompt.strip()}" if user_prompt.strip() else ""
    return (
        "Use the Provider Config Sync MCP tools to automatically reconcile known agent provider configs.\n\n"
        "Scope:\n"
        + "\n".join(f"- {scope}" for scope in scopes)
        + "\n\n"
        "For every scope, list provider config capabilities, inspect entries with diffs or missing provider files, "
        "choose the best source content, update the unified capability first, then apply it to every provider-specific "
        "target that has an equivalent native config. Preserve provider-specific extensions when they are not equivalent "
        "common fields. Do not edit unrelated files. Use expected_content, expected_source, and expected_target from the "
        "latest reads before every write/apply. Report what changed and what was already aligned."
        + extra
    )


def _claude_command(prompt: str, cwd: Path, config_path: Path | None, temp_dir: Path) -> tuple[list[str], dict[str, str]]:
    command, args = _mcp_command()
    mcp_path = temp_dir / "claude-mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    _SERVER_NAME: {
                        "command": command,
                        "args": args,
                        "env": _mcp_env(config_path),
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return [
        "claude",
        "--print",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        ",".join(f"mcp__{_SERVER_NAME}__{tool}" for tool in _SYNC_TOOLS),
        "--mcp-config",
        str(mcp_path),
        prompt,
    ], {}


def _codex_config_arg(name: str, value: Any) -> str:
    return f"{name}={json.dumps(value)}"


def _codex_command(prompt: str, cwd: Path, config_path: Path | None, _temp_dir: Path) -> tuple[list[str], dict[str, str]]:
    command, args = _mcp_command()
    env = _mcp_env(config_path)
    cmd = [
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        "--cd",
        str(cwd),
        "--sandbox",
        "workspace-write",
        "-c",
        _codex_config_arg(f"mcp_servers.{_SERVER_NAME}.command", command),
        "-c",
        _codex_config_arg(f"mcp_servers.{_SERVER_NAME}.args", args),
        "-c",
        _codex_config_arg(f"mcp_servers.{_SERVER_NAME}.enabled_tools", _SYNC_TOOLS),
        "-c",
        _codex_config_arg(f"mcp_servers.{_SERVER_NAME}.default_tools_approval_mode", "approve"),
    ]
    for key, value in sorted(env.items()):
        cmd.extend(["-c", _codex_config_arg(f"mcp_servers.{_SERVER_NAME}.env.{key}", value)])
    cmd.append(prompt)
    return cmd, {}


def _gemini_command(prompt: str, cwd: Path, config_path: Path | None, temp_dir: Path) -> tuple[list[str], dict[str, str]]:
    command, args = _mcp_command()
    settings_path = temp_dir / "gemini-system-settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    _SERVER_NAME: {
                        "command": command,
                        "args": args,
                        "env": _mcp_env(config_path),
                        "trust": True,
                        "includeTools": _SYNC_TOOLS,
                    }
                },
                "mcp": {"allowed": [_SERVER_NAME]},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return [
        "gemini",
        "--prompt",
        prompt,
        "--approval-mode",
        "yolo",
        "--allowed-mcp-server-names",
        _SERVER_NAME,
    ], {"GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(settings_path)}


def _build_command(provider: str, prompt: str, cwd: Path, config_path: Path | None, temp_dir: Path) -> tuple[list[str], dict[str, str]]:
    if provider == "claude":
        return _claude_command(prompt, cwd, config_path, temp_dir)
    if provider == "codex":
        return _codex_command(prompt, cwd, config_path, temp_dir)
    if provider == "gemini":
        return _gemini_command(prompt, cwd, config_path, temp_dir)
    raise ValueError(f"unsupported cli: {provider}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provider-config-sync-automate",
        description="Run an agent CLI non-interactively with Provider Config Sync MCP tools.",
    )
    parser.add_argument("--cli", choices=("claude", "codex", "gemini"), default=os.environ.get("PROVIDER_CONFIG_SYNC_CLI", "claude"))
    parser.add_argument("--config", help="Provider Config Sync JSON config path. Defaults to PROVIDER_CONFIG_SYNC_CONFIG.")
    parser.add_argument("--cwd", default=os.getcwd(), help="Working directory for the agent CLI.")
    parser.add_argument("--prompt", default="", help="Additional instruction appended to the reconciliation prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Print the command/environment JSON without running it.")
    return parser


def main() -> None:
    args = _parser().parse_args()
    config_path = _config_path(args.config)
    cwd = _expand(args.cwd)
    projects = _projects(config_path)
    prompt = _automation_prompt(projects, args.prompt)
    with tempfile.TemporaryDirectory(prefix="provider-config-sync-agent-") as raw_temp:
        temp_dir = Path(raw_temp)
        command, extra_env = _build_command(args.cli, prompt, cwd, config_path, temp_dir)
        if args.dry_run:
            print(json.dumps({"command": command, "env": extra_env, "cwd": str(cwd)}, indent=2))
            return
        env = os.environ.copy()
        env.update(extra_env)
        raise SystemExit(subprocess.run(command, cwd=str(cwd), env=env, check=False).returncode)


if __name__ == "__main__":
    main()

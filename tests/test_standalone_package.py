"""Standalone provider-config-sync package smoke test.

Run:
    cd backend && .venv/bin/python scripts/test_provider_sync_standalone_package.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[1]
_PACKAGE_SRC = _ROOT / "packages" / "provider-config-sync-backend" / "src"
if str(_PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_SRC))

from provider_config_sync_backend import api  # noqa: E402
from provider_config_sync_backend.agent_integrations import install_agent_integrations  # noqa: E402
from provider_config_sync_backend.automation import _automation_prompt, _build_command, _capability_worklist, _projects  # noqa: E402
from provider_config_sync_backend.mcp_server import create_server  # noqa: E402
from provider_config_sync_backend.standalone import create_app  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        FAILURES.append(msg)


async def _noop(*_args, **_kwargs):
    return None


def t_standalone_project_mcp_roundtrip() -> None:
    wipe = Path(tempfile.mkdtemp(prefix="provider-config-sync-standalone-"))
    try:
        project = (wipe / "project").resolve()
        project.mkdir()
        sync_home = wipe / "sync-home"
        claude_mcp = project / ".mcp.json"
        claude_mcp.write_text(
            json.dumps({"mcpServers": {"demo": {"command": "echo", "args": ["ok"]}}}, indent=2) + "\n",
            encoding="utf-8",
        )
        api.configure(
            provider_records=lambda: [
                {"id": "claude", "name": "Claude", "kind": "claude", "config_dir": str(wipe / "claude")},
                {"id": "gemini", "name": "Gemini", "kind": "gemini", "config_dir": str(wipe / "gemini")},
            ],
            project_records=lambda: [{"path": str(project), "node_id": "primary"}],
            sync_home=lambda: sync_home,
            encode_project_cwd=lambda value: "encoded-" + str(abs(hash(value))),
            broadcast_changed=_noop,
        )

        payload = api._discover(str(project))
        mcp = next(capability for capability in payload["groups"]["project"] if capability["capability_id"] == "mcp")
        by_kind = {entry["provider_kinds"][0]: entry for entry in mcp["specifics"]}
        check(set(by_kind) == {"claude", "gemini"}, "standalone discovery finds configured providers")
        check(str(sync_home) in mcp["unified"]["path"], "unified tracking lives under injected sync home")
        check(by_kind["claude"]["token_count"] > 0, "provider-specific config token estimate is reported")
        check(mcp["specific_token_count"] >= by_kind["claude"]["token_count"], "capability token estimate totals specifics")
        check(
            any(item["provider_kind"] == "claude" and item["token_count"] > 0 for item in mcp["provider_token_counts"]),
            "capability token estimate is grouped by provider",
        )
        check(payload["token_totals"]["specifics"] >= mcp["specific_token_count"], "response token total includes capability specifics")

        asyncio.run(
            api.apply_native_file(
                api.ApplyNativeFileRequest(
                    cwd=str(project),
                    capability_id="mcp",
                    source_entry_id=by_kind["claude"]["entry_id"],
                    target_entry_id=mcp["unified"]["entry_id"],
                    expected_source=by_kind["claude"]["content"],
                    expected_target=None,
                )
            )
        )
        payload = api._discover(str(project))
        mcp = next(capability for capability in payload["groups"]["project"] if capability["capability_id"] == "mcp")
        by_kind = {entry["provider_kinds"][0]: entry for entry in mcp["specifics"]}
        asyncio.run(
            api.apply_native_file(
                api.ApplyNativeFileRequest(
                    cwd=str(project),
                    capability_id="mcp",
                    source_entry_id=mcp["unified"]["entry_id"],
                    target_entry_id=by_kind["gemini"]["entry_id"],
                    expected_source=mcp["unified"]["content"],
                    expected_target=None,
                )
            )
        )
        gemini_settings = project / ".gemini" / "settings.json"
        data = json.loads(gemini_settings.read_text(encoding="utf-8"))
        check(data["mcpServers"]["demo"]["command"] == "echo", "standalone apply writes provider-native target")
    finally:
        shutil.rmtree(wipe)


def t_standalone_app_loads_json_config() -> None:
    wipe = Path(tempfile.mkdtemp(prefix="provider-config-sync-standalone-app-"))
    try:
        project = (wipe / "project").resolve()
        project.mkdir()
        config_path = wipe / "provider-config-sync.json"
        config_path.write_text(
            json.dumps(
                {
                    "sync_home": str(wipe / "sync-home"),
                    "providers": [
                        {"id": "claude", "name": "Claude", "kind": "claude", "config_dir": str(wipe / "claude")}
                    ],
                    "projects": [{"path": str(project), "node_id": "primary"}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        app = create_app(config_path)
        paths = {route.path for route in app.routes}
        check("/api/provider-config-sync" in paths, "standalone FastAPI app mounts provider-config-sync route")
    finally:
        shutil.rmtree(wipe)


def t_mcp_server_exposes_sync_tools() -> None:
    server = create_server()
    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    check(
        {
            "open_provider_config_sync_gui",
            "list_provider_config_worklist",
            "list_provider_config_capabilities",
            "read_provider_config_entry",
            "write_provider_config_entry",
            "apply_provider_config_entry",
            "upsert_unified_capability_item",
            "remove_unified_capability_item",
        }.issubset(names),
        "MCP server exposes provider config sync tools",
    )
    gui_tool = next(tool for tool in tools if tool.name == "open_provider_config_sync_gui")
    check(
        gui_tool.meta["ui"]["resourceUri"] == "ui://provider-config-sync/main",
        "Goose GUI tool declares MCP App resource metadata",
    )
    resources = asyncio.run(server.list_resources())
    resource = next(item for item in resources if str(item.uri) == "ui://provider-config-sync/main")
    check(resource.mimeType == "text/html;profile=mcp-app", "Goose GUI resource uses MCP App HTML mime type")
    content = list(asyncio.run(server.read_resource("ui://provider-config-sync/main")))[0]
    check("tools/call" in content.content, "Goose GUI can call provider config sync tools")
    check("Save source before applying" in content.content, "Goose GUI blocks apply while source edits are unsaved")
    check('"reset").onclick = () => { $("content").value = state.original; renderTargets(); }' in content.content, "Goose GUI reset restores apply buttons")
    check("@media (max-width: 760px)" in content.content and "overflow-x: auto" in content.content, "Goose GUI has mobile layout rules")
    check('window.addEventListener("resize", () => app.resize())' in content.content, "Goose GUI notifies host after viewport changes")
    result = asyncio.run(server.call_tool("open_provider_config_sync_gui", {"cwd": "/repo"}))
    check(result.structuredContent["cwd"] == "/repo", "Goose GUI tool returns requested project path")


def t_agent_integrations_install_native_commands() -> None:
    wipe = Path(tempfile.mkdtemp(prefix="provider-config-sync-agent-integrations-"))
    old_home = os.environ.get("HOME")
    try:
        os.environ["HOME"] = str(wipe)
        results = install_agent_integrations()
        check(all(line.startswith("wrote:") for line in results), "agent integration installer writes native commands")
        check((wipe / ".claude" / "commands" / "provider-config-sync.md").is_file(), "Claude command is installed")
        check((wipe / ".codex" / "prompts" / "provider-config-sync.md").is_file(), "Codex prompt is installed")
        check((wipe / ".gemini" / "commands" / "provider-config-sync.toml").is_file(), "Gemini command is installed")
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        shutil.rmtree(wipe)


def t_automation_builds_noninteractive_agent_commands() -> None:
    wipe = Path(tempfile.mkdtemp(prefix="provider-config-sync-automation-"))
    try:
        project = (wipe / "project").resolve()
        project.mkdir()
        config_path = wipe / "provider-config-sync.json"
        config_path.write_text(
            json.dumps(
                {
                    "sync_home": str(wipe / "sync-home"),
                    "providers": [
                        {"id": "claude", "name": "Claude", "kind": "claude", "config_dir": str(wipe / "claude")},
                        {"id": "codex", "name": "Codex", "kind": "codex", "config_dir": str(wipe / "codex")},
                        {"id": "gemini", "name": "Gemini", "kind": "gemini", "config_dir": str(wipe / "gemini")},
                    ],
                    "projects": [{"path": str(project), "node_id": "primary"}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        projects = _projects(config_path)
        worklist = _capability_worklist(projects, config_path)
        prompt = _automation_prompt(projects, "Prefer shortest shared config.")
        check("list_provider_config_worklist" in prompt, "automation prompt uses the worklist tool")
        check("Do not enumerate projects or capabilities yourself" in prompt, "automation prompt forbids agent-side enumeration")
        check(str(project) not in prompt and '"cwd": ""' not in prompt, "automation prompt does not embed the worklist")
        check(any(item["capabilities"] for item in worklist), "worklist tool code enumerates actionable capabilities")
        for provider in ("claude", "codex", "gemini"):
            temp = wipe / provider
            temp.mkdir()
            command, env = _build_command(provider, prompt, project, config_path, temp)
            joined = " ".join(command)
            check("provider_config_sync" in joined or "provider-config-sync" in joined, f"{provider} command injects sync MCP server")
            check(prompt in command, f"{provider} command passes the reconciliation prompt")
            if provider == "claude":
                check("--print" in command and "--mcp-config" in command, "Claude automation is non-interactive with MCP config")
            if provider == "codex":
                check(command[:4] == ["codex", "--ask-for-approval", "never", "exec"], "Codex automation puts approval at root")
                check("--cd" in command and "--sandbox" in command, "Codex automation uses exec workspace mode")
                check(
                    any("default_tools_approval_mode" in item and '"approve"' in item for item in command),
                    "Codex automation approves provider sync MCP tools",
                )
            if provider == "gemini":
                check(command[:2] == ["gemini", "--prompt"], "Gemini automation uses prompt mode")
                check("GEMINI_CLI_SYSTEM_SETTINGS_PATH" in env, "Gemini automation uses temporary system settings")
    finally:
        shutil.rmtree(wipe)


def t_standalone_commands_convert_provider_formats() -> None:
    wipe = Path(tempfile.mkdtemp(prefix="provider-config-sync-commands-"))
    try:
        project = (wipe / "project").resolve()
        project.mkdir()
        claude_command = project / ".claude" / "commands" / "review.md"
        claude_command.parent.mkdir(parents=True)
        claude_command.write_text(
            "---\n"
            "description: Review code\n"
            "allowed-tools: Read, Grep\n"
            "---\n"
            "Review the changed files.\n",
            encoding="utf-8",
        )
        api.configure(
            provider_records=lambda: [
                {"id": "claude", "name": "Claude", "kind": "claude", "config_dir": str(wipe / "claude")},
                {"id": "gemini", "name": "Gemini", "kind": "gemini", "config_dir": str(wipe / "gemini")},
                {"id": "codex", "name": "Codex", "kind": "codex", "config_dir": str(wipe / "codex")},
            ],
            project_records=lambda: [{"path": str(project), "node_id": "primary"}],
            sync_home=lambda: wipe / "sync-home",
            broadcast_changed=_noop,
        )

        payload = api._discover(str(project))
        command = next(capability for capability in payload["groups"]["project"] if capability["capability_id"] == "command-review")
        by_kind = {entry["provider_kinds"][0]: entry for entry in command["specifics"]}
        check(set(by_kind) == {"claude", "gemini"}, "project commands offer Claude and Gemini targets")
        check(json.loads(by_kind["claude"]["content"])["metadata"]["allowed-tools"] == "Read, Grep", "Claude command metadata is normalized")

        asyncio.run(
            api.apply_native_file(
                api.ApplyNativeFileRequest(
                    cwd=str(project),
                    capability_id="command-review",
                    source_entry_id=by_kind["claude"]["entry_id"],
                    target_entry_id=command["unified"]["entry_id"],
                    expected_source=by_kind["claude"]["content"],
                    expected_target=None,
                )
            )
        )
        payload = api._discover(str(project))
        command = next(capability for capability in payload["groups"]["project"] if capability["capability_id"] == "command-review")
        by_kind = {entry["provider_kinds"][0]: entry for entry in command["specifics"]}
        asyncio.run(
            api.apply_native_file(
                api.ApplyNativeFileRequest(
                    cwd=str(project),
                    capability_id="command-review",
                    source_entry_id=command["unified"]["entry_id"],
                    target_entry_id=by_kind["gemini"]["entry_id"],
                    expected_source=command["unified"]["content"],
                    expected_target=None,
                )
            )
        )
        gemini_command = project / ".gemini" / "commands" / "review.toml"
        gemini_data = tomllib.loads(gemini_command.read_text(encoding="utf-8"))
        check(gemini_data["description"] == "Review code", "Gemini command gets description")
        check(gemini_data["prompt"] == "Review the changed files.\n", "Gemini command gets prompt")

        payload = api._discover("")
        check("command-review" not in {capability["capability_id"] for capability in payload["groups"]["global"]}, "global command absent before Codex prompt exists")
        codex_prompt = wipe / "codex" / "prompts" / "review.md"
        codex_prompt.parent.mkdir(parents=True)
        codex_prompt.write_text("Review the worktree.\n", encoding="utf-8")
        payload = api._discover("")
        global_command = next(capability for capability in payload["groups"]["global"] if capability["capability_id"] == "command-review")
        by_kind = {entry["provider_kinds"][0]: entry for entry in global_command["specifics"]}
        check("codex" in by_kind, "Codex custom prompt appears as global command")
    finally:
        shutil.rmtree(wipe)


def t_auto_sync_applies_auto_and_reviews_per_hunk() -> None:
    wipe = Path(tempfile.mkdtemp(prefix="provider-config-sync-auto-"))
    try:
        project = (wipe / "project").resolve()
        project.mkdir()
        claude = project / "CLAUDE.md"
        claude.write_text("alpha\nBRAVO\ncharlie\ndelta\n", encoding="utf-8")
        api.configure(
            provider_records=lambda: [{"id": "claude", "name": "Claude", "kind": "claude", "config_dir": str(wipe / "claude")}],
            project_records=lambda: [{"path": str(project), "node_id": "primary"}],
            sync_home=lambda: wipe / "sync-home",
            broadcast_changed=_noop,
        )
        payload = api._discover(str(project))
        instructions = next(capability for capability in payload["groups"]["project"] if capability["capability_id"] == "instructions")
        unified = instructions["unified"]
        by_kind = {entry["provider_kinds"][0]: entry for entry in instructions["specifics"]}
        unified_path = Path(unified["path"])
        unified_path.parent.mkdir(parents=True, exist_ok=True)
        unified_path.write_text("alpha\nbravo\ncharlie\n", encoding="utf-8")

        payload = api._discover(str(project))
        instructions = next(capability for capability in payload["groups"]["project"] if capability["capability_id"] == "instructions")
        unified = instructions["unified"]
        by_kind = {entry["provider_kinds"][0]: entry for entry in instructions["specifics"]}
        result = asyncio.run(
            api.auto_sync(
                api.AutoSyncRequest(
                    cwd=str(project),
                    capability_id="instructions",
                    source_entry_id=unified["entry_id"],
                    target_entry_id=by_kind["claude"]["entry_id"],
                    expected_source=unified["content"],
                    expected_target=by_kind["claude"]["content"],
                    policy=api.AutoSyncPolicy(additive="off", removal="review", change="auto"),
                )
            )
        )
        check(result["applied_count"] == 1, "auto sync applies edit hunks")
        check(result["pending_count"] == 1, "auto sync returns removal hunks for approval")
        check(claude.read_text(encoding="utf-8") == "alpha\nbravo\ncharlie\ndelta\n", "auto sync leaves reviewed removal hunk untouched")

        payload = api._discover(str(project))
        instructions = next(capability for capability in payload["groups"]["project"] if capability["capability_id"] == "instructions")
        unified = instructions["unified"]
        by_kind = {entry["provider_kinds"][0]: entry for entry in instructions["specifics"]}
        pending = next(item for item in result["log_head"] if item["status"] == "pending")
        asyncio.run(
            api.auto_sync(
                api.AutoSyncRequest(
                    cwd=str(project),
                    capability_id="instructions",
                    source_entry_id=unified["entry_id"],
                    target_entry_id=by_kind["claude"]["entry_id"],
                    expected_source=unified["content"],
                    expected_target=by_kind["claude"]["content"],
                    policy=api.AutoSyncPolicy(additive="off", removal="review", change="auto"),
                    approved_hunk_ids=[pending["hunk_id"]],
                )
            )
        )
        check(claude.read_text(encoding="utf-8") == "alpha\nbravo\ncharlie\n", "approved hunk applies per hunk")
    finally:
        shutil.rmtree(wipe)


def main() -> int:
    t_standalone_project_mcp_roundtrip()
    t_standalone_app_loads_json_config()
    t_mcp_server_exposes_sync_tools()
    t_agent_integrations_install_native_commands()
    t_automation_builds_noninteractive_agent_commands()
    t_standalone_commands_convert_provider_formats()
    t_auto_sync_applies_auto_and_reviews_per_hunk()
    if FAILURES:
        print(f"\nFAILED: {len(FAILURES)}")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

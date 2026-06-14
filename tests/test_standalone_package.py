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
_UI_STYLES = _ROOT / "packages" / "provider-config-sync-ui" / "src" / "styles.css"
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


def t_diff_line_editors_hide_textarea_chrome() -> None:
    styles = _UI_STYLES.read_text(encoding="utf-8")
    editor_rule = styles.split(".provider-sync-aligned-diff-cell-editor {", 1)[1].split("}", 1)[0]
    check("resize: none;" in editor_rule, "diff line editors disable manual resize")
    check("overflow: hidden;" in editor_rule, "diff line editors hide textarea scroll chrome")
    check("scrollbar-width: none;" in editor_rule, "diff line editors hide firefox scrollbars")
    check(
        ".provider-sync-aligned-diff-cell-editor::-webkit-resizer" in styles,
        "diff line editors hide webkit resize handles",
    )
    check(
        ".provider-sync-aligned-diff-cell-editor::-webkit-scrollbar" in styles,
        "diff line editors hide webkit scrollbars",
    )


def t_diff_views_use_natural_height_without_internal_scrolling() -> None:
    styles = _UI_STYLES.read_text(encoding="utf-8")
    for selector in (
        ".provider-sync-main",
        ".provider-sync-editor-card",
        ".provider-sync-structured",
        ".provider-sync-aligned-diff",
        ".provider-sync-aligned-diff-body",
        ".provider-sync-specifics-card",
        ".provider-sync-specifics",
    ):
        rule = styles.split(f"{selector} {{", 1)[1].split("}", 1)[0]
        check("overflow: visible;" in rule, f"{selector} does not create an internal scroll view")

    shell_rule = styles.split(".provider-sync-shell {", 1)[1].split("}", 1)[0]
    editor_grid_rule = styles.split(".provider-sync-editor-grid {", 1)[1].split("}", 1)[0]
    diff_body_rule = styles.split(".provider-sync-aligned-diff-body {", 1)[1].split("}", 1)[0]
    specifics_rule = styles.split(".provider-sync-specifics {", 1)[1].split("}", 1)[0]
    check("flex: 1 0 auto;" in shell_rule, "shell can grow past the viewport")
    check("flex: 0 0 auto;" in editor_grid_rule, "editor grid uses content height")
    check("flex: 0 0 auto;" in diff_body_rule, "diff body uses content height")
    check("flex: 0 0 auto;" in specifics_rule, "specifics view uses content height")


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
            "auto_sync_provider_config_entry",
            "create_provider_config_capability",
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
    check("create_provider_config_capability" in content.content, "Goose GUI can create capabilities")
    check('id="createCapability"' in content.content, "Goose GUI exposes add capability controls")
    check("collapsedGroups" in content.content and "cap-group-head" in content.content, "Goose GUI can collapse capability groups")
    check("auto_sync_provider_config_entry" in content.content, "Goose GUI can auto-merge with configured AI review")
    check("<span>Unified</span>" in content.content and "<span>Specific</span>" in content.content, "Goose GUI always labels unified and specific diff panes")
    check("Unified is missing" in content.content and "Specific is missing" in content.content, "Goose GUI keeps empty diff panes visible")
    check("Save source before applying" in content.content, "Goose GUI blocks apply while source edits are unsaved")
    check('"reset").onclick = () => { $("content").value = state.original;' in content.content, "Goose GUI reset restores apply buttons")
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
        check(all(line.startswith("wrote:") for line in results), "agent integration installer writes native commands/skills")
        check((wipe / ".claude" / "commands" / "provider-config-sync.md").is_file(), "Claude command is installed")
        codex_skill = wipe / ".agents" / "skills" / "provider-config-sync" / "SKILL.md"
        check(codex_skill.is_file(), "Codex skill is installed")
        check("name: provider-config-sync" in codex_skill.read_text(encoding="utf-8"), "Codex skill has required frontmatter")
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
    old_home = os.environ.get("HOME")
    try:
        os.environ["HOME"] = str(wipe)
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
        check(set(by_kind) == {"claude", "codex", "gemini"}, "project commands offer Claude, Codex, and Gemini targets")
        check(command["name"] == "Command/skill: review", "command capability label distinguishes Codex skills")
        check(by_kind["claude"]["label"] == "Claude command", "Claude command label is provider-specific")
        check(by_kind["codex"]["label"] == "Codex skill", "Codex command target is a skill")
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
        asyncio.run(
            api.apply_native_file(
                api.ApplyNativeFileRequest(
                    cwd=str(project),
                    capability_id="command-review",
                    source_entry_id=command["unified"]["entry_id"],
                    target_entry_id=by_kind["codex"]["entry_id"],
                    expected_source=command["unified"]["content"],
                    expected_target=None,
                )
            )
        )
        codex_skill = project / ".agents" / "skills" / "command-review" / "SKILL.md"
        codex_skill_content = codex_skill.read_text(encoding="utf-8")
        check("name: command-review" in codex_skill_content, "Codex command skill gets a command-scoped skill name")
        check("description: Review code" in codex_skill_content, "Codex command skill gets description")
        check("provider-config-sync-description: Review code" in codex_skill_content, "Codex command skill preserves command description")
        check("Review the changed files." in codex_skill_content, "Codex command skill gets instructions")

        payload = api._discover("")
        check("command-review" not in {capability["capability_id"] for capability in payload["groups"]["global"]}, "global command absent before Codex command skill exists")
        codex_skill = wipe / ".agents" / "skills" / "command-review" / "SKILL.md"
        codex_skill.parent.mkdir(parents=True)
        codex_skill.write_text(
            "---\n"
            "name: command-review\n"
            "description: Review code\n"
            "provider-config-sync-kind: command\n"
            "provider-config-sync-name: review\n"
            "---\n"
            "Review the worktree.\n",
            encoding="utf-8",
        )
        payload = api._discover("")
        global_command = next(capability for capability in payload["groups"]["global"] if capability["capability_id"] == "command-review")
        by_kind = {entry["provider_kinds"][0]: entry for entry in global_command["specifics"]}
        check("codex" in by_kind, "Codex command skill appears as global command")
        check(by_kind["codex"]["label"] == "Codex skill", "Codex skill label is provider-specific")
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        shutil.rmtree(wipe)


def t_create_capability_adds_provider_native_seed() -> None:
    wipe = Path(tempfile.mkdtemp(prefix="provider-config-sync-create-capability-"))
    try:
        project = (wipe / "project").resolve()
        project.mkdir()
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
        result = asyncio.run(
            api.create_capability(
                api.CreateCapabilityRequest(
                    cwd=str(project),
                    scope="project",
                    category="command",
                    provider_kinds=["claude", "gemini", "codex"],
                    name="ship",
                    description="Ship changes",
                    instructions="Review, test, and ship.",
                    metadata={"allowed-tools": "Read, Bash"},
                )
            )
        )
        check(result["capability"]["capability_id"] == "command-ship", "create capability returns rediscovered capability")
        created = project / ".claude" / "commands" / "ship.md"
        gemini = project / ".gemini" / "commands" / "ship.toml"
        codex = project / ".agents" / "skills" / "command-ship" / "SKILL.md"
        unified = Path(result["capability"]["unified"]["path"])
        check(unified.is_file(), "create capability writes unified seed file")
        check(created.is_file(), "create capability writes Claude provider-native seed file")
        check(gemini.is_file(), "create capability writes Gemini provider-native seed file")
        check(codex.is_file(), "create capability writes Codex provider-native seed file")
        payload = api._discover(str(project))
        command = next(capability for capability in payload["groups"]["project"] if capability["capability_id"] == "command-ship")
        by_kind = {entry["provider_kinds"][0]: entry for entry in command["specifics"]}
        check(set(by_kind) == {"claude", "codex", "gemini"}, "created capability discovers all provider targets")
        check(json.loads(command["unified"]["content"])["instructions"] == "Review, test, and ship.\n", "create capability seeds unified")
        check(json.loads(by_kind["claude"]["content"])["metadata"]["allowed-tools"] == "Read, Bash", "create capability preserves metadata")
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


def t_auto_sync_llm_mode_uses_configured_reviewer() -> None:
    wipe = Path(tempfile.mkdtemp(prefix="provider-config-sync-llm-"))
    try:
        project = (wipe / "project").resolve()
        project.mkdir()
        claude = project / "CLAUDE.md"
        claude.write_text("alpha\nextra\n", encoding="utf-8")

        def review(context: dict) -> list[str]:
            check(context["target_side"] == "specific", "LLM reviewer receives target side")
            return [item["hunk_id"] for item in context["candidates"] if item["operation"] == "removal"]

        api.configure(
            provider_records=lambda: [{"id": "claude", "name": "Claude", "kind": "claude", "config_dir": str(wipe / "claude")}],
            project_records=lambda: [{"path": str(project), "node_id": "primary"}],
            sync_home=lambda: wipe / "sync-home",
            broadcast_changed=_noop,
            llm_review=review,
        )
        payload = api._discover(str(project))
        instructions = next(capability for capability in payload["groups"]["project"] if capability["capability_id"] == "instructions")
        unified = instructions["unified"]
        Path(unified["path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(unified["path"]).write_text("alpha\n", encoding="utf-8")

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
                    policy=api.AutoSyncPolicy(additive="off", removal="llm", change="off"),
                )
            )
        )
        check(result["applied_count"] == 1, "LLM mode applies reviewer-approved hunk")
        check(claude.read_text(encoding="utf-8") == "alpha\n", "LLM-approved hunk updates target")
    finally:
        shutil.rmtree(wipe)


def t_auto_sync_llm_can_review_one_hunk() -> None:
    wipe = Path(tempfile.mkdtemp(prefix="provider-config-sync-llm-hunk-"))
    try:
        project = (wipe / "project").resolve()
        project.mkdir()
        claude = project / "CLAUDE.md"
        claude.write_text("alpha\nbravo\ncharlie\ndelta\n", encoding="utf-8")
        reviewed: list[str] = []

        def review(context: dict) -> list[str]:
            reviewed.extend(item["hunk_id"] for item in context["candidates"])
            return reviewed

        api.configure(
            provider_records=lambda: [{"id": "claude", "name": "Claude", "kind": "claude", "config_dir": str(wipe / "claude")}],
            project_records=lambda: [{"path": str(project), "node_id": "primary"}],
            sync_home=lambda: wipe / "sync-home",
            broadcast_changed=_noop,
            llm_review=review,
        )
        payload = api._discover(str(project))
        instructions = next(capability for capability in payload["groups"]["project"] if capability["capability_id"] == "instructions")
        unified = instructions["unified"]
        Path(unified["path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(unified["path"]).write_text("alpha\nbravo\ncharlie\n", encoding="utf-8")

        payload = api._discover(str(project))
        instructions = next(capability for capability in payload["groups"]["project"] if capability["capability_id"] == "instructions")
        unified = instructions["unified"]
        by_kind = {entry["provider_kinds"][0]: entry for entry in instructions["specifics"]}
        preview = asyncio.run(api.auto_sync(api.AutoSyncRequest(
            cwd=str(project),
            capability_id="instructions",
            source_entry_id=unified["entry_id"],
            target_entry_id=by_kind["claude"]["entry_id"],
            expected_source=unified["content"],
            expected_target=by_kind["claude"]["content"],
            policy=api.AutoSyncPolicy(additive="off", removal="review", change="off"),
        )))
        pending = next(item for item in preview["log_head"] if item["status"] == "pending")
        result = asyncio.run(api.auto_sync(api.AutoSyncRequest(
            cwd=str(project),
            capability_id="instructions",
            source_entry_id=unified["entry_id"],
            target_entry_id=by_kind["claude"]["entry_id"],
            expected_source=unified["content"],
            expected_target=by_kind["claude"]["content"],
            policy=api.AutoSyncPolicy(additive="off", removal="off", change="off"),
            llm_hunk_ids=[pending["hunk_id"]],
        )))
        check(reviewed == [pending["hunk_id"]], "LLM reviewer receives only requested hunk")
        check(result["applied_count"] == 1, "LLM-approved hunk applies")
        check(claude.read_text(encoding="utf-8") == "alpha\nbravo\ncharlie\n", "single LLM hunk updates target")
    finally:
        shutil.rmtree(wipe)


def t_auto_sync_settings_resolve_hierarchy() -> None:
    wipe = Path(tempfile.mkdtemp(prefix="provider-config-sync-settings-"))
    try:
        project = (wipe / "project").resolve()
        project.mkdir()
        api.configure(
            provider_records=lambda: [{"id": "claude", "name": "Claude", "kind": "claude", "config_dir": str(wipe / "claude")}],
            project_records=lambda: [{"path": str(project), "node_id": "primary"}],
            sync_home=lambda: wipe / "sync-home",
            broadcast_changed=_noop,
        )
        initial = api.get_auto_sync_settings(str(project), "instructions")
        check(initial["effective"] == {"additive": "off", "removal": "off", "change": "off"}, "initial settings are off")
        api.update_auto_sync_settings(api.AutoSyncSettingsPatch(
            level="global",
            policy={"additive": "auto", "removal": "off", "change": "off"},
        ))
        api.update_auto_sync_settings(api.AutoSyncSettingsPatch(
            level="capability",
            capability_id="instructions",
            policy={"change": "review"},
        ))
        api.update_auto_sync_settings(api.AutoSyncSettingsPatch(
            level="project",
            cwd=str(project),
            policy={"removal": "llm"},
        ))
        resolved = api.update_auto_sync_settings(api.AutoSyncSettingsPatch(
            level="project_capability",
            cwd=str(project),
            capability_id="instructions",
            policy={"change": "auto"},
        ))
        check(resolved["effective"] == {"additive": "auto", "removal": "llm", "change": "auto"}, "deepest settings override")
        cleared = api.update_auto_sync_settings(api.AutoSyncSettingsPatch(
            level="project_capability",
            cwd=str(project),
            capability_id="instructions",
            policy={"change": "inherit"},
        ))
        check(cleared["effective"] == {"additive": "auto", "removal": "llm", "change": "review"}, "inherit removes deepest override")
    finally:
        shutil.rmtree(wipe)


def main() -> int:
    t_standalone_project_mcp_roundtrip()
    t_standalone_app_loads_json_config()
    t_diff_line_editors_hide_textarea_chrome()
    t_diff_views_use_natural_height_without_internal_scrolling()
    t_mcp_server_exposes_sync_tools()
    t_agent_integrations_install_native_commands()
    t_automation_builds_noninteractive_agent_commands()
    t_standalone_commands_convert_provider_formats()
    t_create_capability_adds_provider_native_seed()
    t_auto_sync_applies_auto_and_reviews_per_hunk()
    t_auto_sync_llm_mode_uses_configured_reviewer()
    t_auto_sync_llm_can_review_one_hunk()
    t_auto_sync_settings_resolve_hierarchy()
    if FAILURES:
        print(f"\nFAILED: {len(FAILURES)}")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

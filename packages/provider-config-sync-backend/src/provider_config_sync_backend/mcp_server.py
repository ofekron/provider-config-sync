from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from . import api
from .automation import _capability_worklist, _config_path, _projects
from .mcp_app import MCP_APP_MIME_TYPE, MCP_APP_URI, mcp_app_html
from .standalone import configure_from_file


def _error(error: HTTPException) -> ValueError:
    return ValueError(str(error.detail))


def _core_env(name: str) -> str:
    for prefix in ("BETTER_CLAUDE_", "BETTER_AGENT_"):
        value = os.environ.get(prefix + name)
        if value and value.strip():
            return value.strip()
    return ""


def _session_capabilities_request(method: str, body: dict | None = None) -> dict[str, Any]:
    """Call the Better Agent core capabilities endpoint for the current session.
    Core owns the active-capability write; this MCP is the authorized trigger."""
    backend_url = _core_env("BACKEND_URL") or "http://localhost:8000"
    token = _core_env("INTERNAL_TOKEN")
    sid = _core_env("APP_SESSION_ID")
    if not sid:
        raise ValueError("no active session (BETTER_CLAUDE_APP_SESSION_ID unset)")
    if not token:
        raise ValueError("capabilities require backend auth (no internal token)")
    url = f"{backend_url.rstrip('/')}/api/internal/sessions/{sid}/capabilities"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"X-Internal-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise ValueError(f"capability {method} failed ({error.code}): {detail}") from error


def create_server() -> FastMCP:
    configure_from_file()
    server = FastMCP(
        "Provider Config Sync",
        instructions=(
            "Discover, inspect, edit, and sync provider-native AI agent configuration files "
            "across Claude, Codex, and Gemini."
        ),
        website_url="https://github.com/ofekron/provider-config-sync",
    )

    @server.resource(
        MCP_APP_URI,
        name="Provider Config Sync",
        title="Provider Config Sync",
        description="Interactive MCP App for syncing provider-native agent config capabilities.",
        mime_type=MCP_APP_MIME_TYPE,
        meta={
            "ui": {
                "csp": {
                    "connectDomains": [],
                    "resourceDomains": [],
                    "frameDomains": [],
                    "baseUriDomains": [],
                },
                "prefersBorder": True,
            }
        },
    )
    def provider_config_sync_mcp_app() -> str:
        return mcp_app_html()

    @server.tool(
        meta={
            "ui": {
                "resourceUri": MCP_APP_URI,
                "visibility": ["model", "app"],
            }
        }
    )
    def open_provider_config_sync_gui(cwd: str = "") -> CallToolResult:
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        "Provider Config Sync GUI opened. "
                        f"Project path: {cwd or 'configured default/current project'}"
                    ),
                )
            ],
            structuredContent={"cwd": cwd},
            meta={"ui": {"resourceUri": MCP_APP_URI}},
        )

    @server.tool()
    async def list_provider_config_projects() -> dict[str, Any]:
        try:
            return await api.list_provider_config_sync_projects()
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    def get_provider_config_state(cwd: str = "") -> dict[str, Any]:
        try:
            return api._discover(cwd)
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    def list_provider_config_capability_picker(cwd: str = "") -> dict[str, Any]:
        try:
            return api.list_capability_picker_sources(cwd)
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    def list_provider_config_worklist() -> dict[str, Any]:
        try:
            config_path = _config_path(None)
            projects = _projects(config_path)
            return {"worklist": _capability_worklist(projects, config_path)}
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    def list_provider_config_capabilities(cwd: str = "") -> dict[str, Any]:
        try:
            payload = api._discover(cwd)
        except HTTPException as error:
            raise _error(error) from error
        capabilities = []
        for capability in payload["capabilities"]:
            capabilities.append(
                {
                    "id": capability["id"],
                    "capability_id": capability["capability_id"],
                    "name": capability["name"],
                    "scope": capability["scope"],
                    "category": capability["category"],
                    "language": capability["language"],
                    "has_diffs": capability["has_diffs"],
                    "specific_count": capability["specific_count"],
                    "missing_count": capability["missing_count"],
                    "unified_token_count": capability["unified_token_count"],
                    "specific_token_count": capability["specific_token_count"],
                    "total_token_count": capability["total_token_count"],
                    "provider_token_counts": capability["provider_token_counts"],
                    "unified": _entry_summary(capability["unified"]),
                    "specifics": [_entry_summary(entry) for entry in capability["specifics"]],
                }
            )
        return {"capabilities": capabilities, "providers": payload["providers"], "token_totals": payload["token_totals"]}

    @server.tool()
    def read_provider_config_entry(cwd: str = "", entry_id: str | None = None, path: str | None = None) -> dict[str, Any]:
        try:
            entries = api._entry_map(cwd)
            entry = entries.get(entry_id or path or "")
            if entry is None:
                raise HTTPException(status_code=400, detail="entry was not found")
            return entry
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    async def update_provider_config_auto_settings(
        level: str,
        policy: dict[str, str],
        cwd: str = "",
        capability_id: str = "",
    ) -> dict[str, Any]:
        try:
            return api.update_auto_sync_settings(
                api.AutoSyncSettingsPatch(
                    level=level,
                    cwd=cwd,
                    capability_id=capability_id,
                    policy=policy,
                )
            )
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    async def write_provider_config_entry(
        content: str,
        expected_content: str | None,
        cwd: str = "",
        entry_id: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await api.write_native_file(
                api.WriteNativeFileRequest(
                    cwd=cwd,
                    entry_id=entry_id,
                    path=path,
                    expected_content=expected_content,
                    content=content,
                )
            )
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    async def restore_provider_config_entry(
        expected_content: str | None,
        cwd: str = "",
        entry_id: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await api.restore_native_file(
                api.RestoreNativeFileRequest(
                    cwd=cwd,
                    entry_id=entry_id,
                    path=path,
                    expected_content=expected_content,
                )
            )
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    async def delete_provider_config_capability(
        expected_contents: dict[str, str | None],
        cwd: str = "",
        scope: str | None = None,
        capability_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await api.delete_capability(
                api.DeleteCapabilityRequest(
                    cwd=cwd,
                    scope=scope,
                    capability_id=capability_id,
                    expected_contents=expected_contents,
                )
            )
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    async def apply_provider_config_entry(
        capability_id: str,
        source_entry_id: str,
        target_entry_id: str,
        expected_source: str,
        expected_target: str | None,
        cwd: str = "",
    ) -> dict[str, Any]:
        try:
            return await api.apply_native_file(
                api.ApplyNativeFileRequest(
                    cwd=cwd,
                    capability_id=capability_id,
                    source_entry_id=source_entry_id,
                    target_entry_id=target_entry_id,
                    expected_source=expected_source,
                    expected_target=expected_target,
                )
            )
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    async def auto_sync_provider_config_entry(
        capability_id: str,
        source_entry_id: str,
        target_entry_id: str,
        expected_source: str,
        expected_target: str | None,
        policy: dict[str, str],
        cwd: str = "",
        approved_hunk_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            return await api.auto_sync(
                api.AutoSyncRequest(
                    cwd=cwd,
                    capability_id=capability_id,
                    source_entry_id=source_entry_id,
                    target_entry_id=target_entry_id,
                    expected_source=expected_source,
                    expected_target=expected_target,
                    policy=api.AutoSyncPolicy(**policy),
                    approved_hunk_ids=approved_hunk_ids or [],
                )
            )
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    async def create_provider_config_capability(
        scope: str,
        category: str,
        provider_kinds: list[str],
        name: str,
        description: str = "",
        instructions: str = "",
        metadata: dict[str, Any] | None = None,
        cwd: str = "",
    ) -> dict[str, Any]:
        try:
            return await api.create_capability(
                api.CreateCapabilityRequest(
                    cwd=cwd,
                    scope=scope,
                    category=category,
                    provider_kinds=provider_kinds,
                    name=name,
                    description=description,
                    instructions=instructions,
                    metadata=metadata or {},
                )
            )
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    async def transfer_provider_config_capability(
        scope: str,
        capability_id: str,
        target_scope: str,
        mode: str,
        expected_contents: dict[str, str | None],
        cwd: str = "",
        target_cwd: str = "",
    ) -> dict[str, Any]:
        try:
            return await api.transfer_capability(
                api.TransferCapabilityRequest(
                    cwd=cwd,
                    scope=scope,
                    capability_id=capability_id,
                    target_cwd=target_cwd,
                    target_scope=target_scope,
                    mode=mode,
                    expected_contents=expected_contents,
                )
            )
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    async def upsert_unified_capability_item(
        capability_id: str,
        item: dict[str, Any],
        cwd: str = "",
        scope: str | None = None,
        item_name: str | None = None,
        expected_content: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await api.upsert_unified_capability_item(
                api.UpsertUnifiedCapabilityItemRequest(
                    cwd=cwd,
                    scope=scope,
                    capability_id=capability_id,
                    item_name=item_name,
                    item=item,
                    expected_content=expected_content,
                )
            )
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    async def remove_unified_capability_item(
        capability_id: str,
        item_name: str,
        cwd: str = "",
        scope: str | None = None,
        expected_content: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await api.remove_unified_capability_item(
                api.RemoveUnifiedCapabilityItemRequest(
                    cwd=cwd,
                    scope=scope,
                    capability_id=capability_id,
                    item_name=item_name,
                    expected_content=expected_content,
                )
            )
        except HTTPException as error:
            raise _error(error) from error

    @server.tool()
    def list_capabilities() -> dict[str, Any]:
        """List the scoped capabilities loadable in the current session and which
        are currently active."""
        return _session_capabilities_request("GET")

    @server.tool()
    def load_capability(capability_id: str) -> dict[str, Any]:
        """Load a scoped capability (e.g. its MCP + skill) into the current
        session. Takes effect on the next turn."""
        return _session_capabilities_request(
            "POST", {"action": "load", "capability_id": capability_id}
        )

    @server.tool()
    def release_capability(capability_id: str) -> dict[str, Any]:
        """Release a previously loaded capability from the current session."""
        return _session_capabilities_request(
            "POST", {"action": "release", "capability_id": capability_id}
        )

    return server


def _entry_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": entry["entry_id"],
        "path": entry["path"],
        "role": entry["role"],
        "label": entry["label"],
        "exists": entry["exists"],
        "writable": entry["writable"],
        "read_error": entry["read_error"],
        "token_count": entry["token_count"],
        "provider_names": entry["provider_names"],
        "provider_kinds": entry["provider_kinds"],
        "content_kind": entry["content_kind"],
    }


def main() -> None:
    create_server().run("stdio")


if __name__ == "__main__":
    main()

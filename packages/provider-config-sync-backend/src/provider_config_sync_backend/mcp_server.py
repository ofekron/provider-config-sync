from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

from . import api
from .standalone import configure_from_file


def _error(error: HTTPException) -> ValueError:
    return ValueError(str(error.detail))


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
                    "unified": _entry_summary(capability["unified"]),
                    "specifics": [_entry_summary(entry) for entry in capability["specifics"]],
                }
            )
        return {"capabilities": capabilities}

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
        "provider_names": entry["provider_names"],
        "provider_kinds": entry["provider_kinds"],
        "content_kind": entry["content_kind"],
    }


def main() -> None:
    create_server().run("stdio")


if __name__ == "__main__":
    main()

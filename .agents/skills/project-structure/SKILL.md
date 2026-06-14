---
name: project-structure
description: Use for provider-config-sync project structure, backend API/MCP UI locations, core diff logic, tests, and run commands.
---

# Provider Config Sync Structure

Provider Config Sync is a small monorepo with a TypeScript core package and a Python backend that exposes discovery, sync, auto-sync, MCP tools, and an embedded HTML UI for comparing unified provider configuration with provider-specific files.

## Routing

- Core diff/item logic: `packages/provider-config-sync-core/src/`
- Backend API, discovery, sync, auto-sync, embedded UI: `packages/provider-config-sync-backend/src/provider_config_sync_backend/api.py`
- MCP server wiring: `packages/provider-config-sync-backend/src/provider_config_sync_backend/mcp_server.py`
- Standalone app entry: `packages/provider-config-sync-backend/src/provider_config_sync_backend/standalone.py`
- Automation integrations: `packages/provider-config-sync-backend/src/provider_config_sync_backend/automation.py`
- Python regression tests: `tests/test_standalone_package.py`
- Root package scripts: `package.json`

## Commands

- Build TypeScript core: `npm run build:core`
- Run Python regression tests: `python tests/test_standalone_package.py`

## Keeping This Skill Current

Agents must update this skill when material project facts change: major directories, API/UI ownership, run commands, persistence locations, or subsystem responsibilities. Keep it current-state only and concise.

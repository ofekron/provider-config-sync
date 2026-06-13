# Provider Config Sync

**One control plane for AI-agent configuration across Claude, Codex, and Gemini.**

Provider Config Sync keeps each provider's native config files intact while giving teams a unified way to compare, edit, and apply equivalent ideas across providers: instructions, memory, MCP servers, skills, and custom agent definitions.

It is built for the messy reality of multi-provider agent workflows: every CLI has its own file layout, config syntax, and extension points. Provider Config Sync abstracts those differences without hiding them, so you can standardize what should be shared and preserve provider-specific power where it matters.

## Why It Exists

AI coding agents are becoming operating environments, and their config files are no longer minor setup details. They shape token usage, tool access, context quality, answer correctness, agent effectiveness, and how much wall-clock time gets spent on every task. A small stale instruction, missing MCP server, or mismatched project memory can quietly make an agent slower, more expensive, less reliable, or just wrong.

That makes these files worth editing deliberately, even manually. Teams now carry important behavior in files like:

- `CLAUDE.md`
- `AGENTS.md`
- `GEMINI.md`
- `.mcp.json`
- provider settings files
- skills and custom subagent definitions
- project memory files

The problem: those files represent the same ideas in different formats.

Provider Config Sync gives those ideas names, tracks a unified version, and shows each provider-specific version side by side so changes can move in either direction.

## What You Get

- **Unified ideas, native files**
  Keep Claude, Codex, and Gemini using their own real config files. Provider Config Sync never replaces the provider-native configuration system.

- **Bidirectional sync**
  Apply changes from unified to provider-specific files, or pull provider-specific changes back into unified tracking.

- **Structured normalization**
  MCP servers, skills, and custom agents are converted into a common editable shape, then written back in each provider's native format.

- **Provider extensions stay visible**
  Common fields can be managed consistently, while provider-only metadata remains editable instead of being discarded.

- **Standalone backend**
  Run the sync engine as a standalone FastAPI service without Better Claude.

- **Reusable frontend core**
  Use the TypeScript diff/item helpers in your own UI.

- **Safer writes**
  Writes use expected-content checks, atomic creation, and first-write backups to avoid silent clobbering.

## Supported Ideas

| Idea | Claude | Codex | Gemini |
| --- | --- | --- | --- |
| General instructions | `CLAUDE.md` | `AGENTS.md` | `GEMINI.md` or configured context file |
| Project memory | Claude project memory | unified idea tracking | unified idea tracking |
| MCP servers | `.mcp.json` / settings | `config.toml` | `settings.json` |
| Skills | `.claude/skills` | `.agents/skills` | `.agents/skills` / `.gemini/skills` |
| Custom agents | Markdown frontmatter | TOML | Markdown frontmatter |
| Provider settings | JSON/settings | TOML | JSON/settings |

## Repository Layout

```text
packages/
  provider-config-sync-backend/
    src/provider_config_sync_backend/
      api.py          # discovery, conversion, apply/write API
      standalone.py   # standalone FastAPI app + CLI entrypoint

  provider-config-sync-core/
    src/
      diff.ts         # aligned diff rows, hunks, line/block apply helpers
      items.ts        # normalized item parsing helpers
```

## Quick Start

Clone and install the standalone backend:

```bash
git clone https://github.com/ofekron/provider-config-sync.git
cd provider-config-sync

python -m venv .venv
. .venv/bin/activate
pip install -e "packages/provider-config-sync-backend[server]"
```

Create a config file:

```json
{
  "sync_home": "~/.provider-config-sync",
  "providers": [
    { "id": "claude", "name": "Claude", "kind": "claude", "config_dir": "~/.claude" },
    { "id": "codex", "name": "Codex", "kind": "codex", "config_dir": "~/.codex" },
    { "id": "gemini", "name": "Gemini", "kind": "gemini", "config_dir": "~/.gemini" }
  ],
  "projects": [
    { "path": "/absolute/path/to/your/project", "node_id": "primary" }
  ]
}
```

Run the API:

```bash
PROVIDER_CONFIG_SYNC_CONFIG=./provider-config-sync.json provider-config-sync-backend
```

Open:

```text
http://127.0.0.1:8765/api/provider-config-sync?cwd=/absolute/path/to/your/project
```

Verify the standalone package:

```bash
python tests/test_standalone_package.py
```

## API Surface

The standalone app mounts:

```text
GET    /api/provider-config-sync
PUT    /api/provider-config-sync/file
POST   /api/provider-config-sync/apply
POST   /api/provider-config-sync/unified-item
DELETE /api/provider-config-sync/unified-item
```

Use `GET /api/provider-config-sync?cwd=...` to discover ideas and file entries. The response includes unified entries and provider-specific entries with `entry_id`, content, existence, writability, diff status, and provider metadata.

## Library Usage

Backend:

```python
from pathlib import Path
from provider_config_sync_backend.api import configure, router

configure(
    provider_records=lambda: [
        {"id": "claude", "name": "Claude", "kind": "claude", "config_dir": "~/.claude"},
        {"id": "codex", "name": "Codex", "kind": "codex", "config_dir": "~/.codex"},
    ],
    project_records=lambda: [{"path": "/repo", "node_id": "primary"}],
    sync_home=lambda: Path("~/.provider-config-sync").expanduser(),
)
```

Frontend:

```ts
import { buildAlignedDiffRows } from "@better-agent/provider-config-sync-core/diff";
import { parseMcpServers } from "@better-agent/provider-config-sync-core/items";
```

## Design Principles

- **Native first**: providers keep reading their own files.
- **Unified is a tracking layer**: it helps compare and propagate equivalent ideas; it is not a runtime replacement for provider config.
- **No silent overwrites**: writes require the expected previous content.
- **Format abstraction, not format erasure**: common fields get a common UI shape, provider-specific metadata survives round trips.
- **Portable core**: the backend package has no Better Claude dependency.

## License

MIT

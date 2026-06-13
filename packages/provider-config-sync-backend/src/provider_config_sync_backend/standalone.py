from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .api import configure, router


def _expand_path(raw: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(raw)))).absolute()


def _read_config(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with _expand_path(path).open("r", encoding="utf-8") as fh:
        value = json.load(fh)
    if not isinstance(value, dict):
        raise ValueError("provider config sync config must be a JSON object")
    return value


def _default_providers() -> list[dict[str, str]]:
    return [
        {"id": "claude", "name": "Claude", "kind": "claude", "config_dir": "~/.claude"},
        {"id": "gemini", "name": "Gemini", "kind": "gemini", "config_dir": "~/.gemini"},
        {"id": "codex", "name": "Codex", "kind": "codex", "config_dir": "~/.codex"},
    ]


def _records(value: object, label: str) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be a list of objects")
    return value


def create_app(config_path: str | Path | None = None) -> FastAPI:
    configure_from_file(config_path)
    app = FastAPI(title="Provider Config Sync")
    app.include_router(router)
    return app


def configure_from_file(config_path: str | Path | None = None) -> None:
    config = _read_config(config_path or os.environ.get("PROVIDER_CONFIG_SYNC_CONFIG"))
    providers = _records(config.get("providers"), "providers") or _default_providers()
    projects = _records(config.get("projects"), "projects")
    sync_home = _expand_path(config.get("sync_home") or os.environ.get("PROVIDER_CONFIG_SYNC_HOME") or "~/.provider-config-sync")
    configure(
        provider_records=lambda: providers,
        project_records=lambda: projects,
        sync_home=lambda: sync_home,
    )


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("PROVIDER_CONFIG_SYNC_HOST") or "127.0.0.1"
    port = int(os.environ.get("PROVIDER_CONFIG_SYNC_PORT") or "8765")
    uvicorn.run("provider_config_sync_backend.standalone:app", host=host, port=port)


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
from importlib.resources import files
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

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
        {"id": "agy", "name": "Antigravity", "kind": "agy", "config_dir": "~/.gemini/antigravity-cli"},
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
    app.add_api_route("/", standalone_app_html, methods=["GET"], include_in_schema=False)
    app.add_api_route("/provider-config-sync", standalone_app_html, methods=["GET"], include_in_schema=False)
    app.add_api_route("/assets/standalone-app.js", standalone_app_js, methods=["GET"], include_in_schema=False)
    app.add_api_route("/assets/standalone-app.css", standalone_app_css, methods=["GET"], include_in_schema=False)
    app.include_router(router)
    return app


def configure_from_file(config_path: str | Path | None = None) -> None:
    config = _read_config(config_path or os.environ.get("PROVIDER_CONFIG_SYNC_CONFIG"))
    providers = _records(config.get("providers"), "providers") or _default_providers()
    projects = _records(config.get("projects"), "projects")
    sync_home = _expand_path(config.get("sync_home") or os.environ.get("PROVIDER_CONFIG_SYNC_HOME") or "~/.provider-config-sync")
    change_webhook_url = config.get("change_webhook_url") or os.environ.get("PROVIDER_CONFIG_SYNC_CHANGE_WEBHOOK_URL")
    change_webhook_token = os.environ.get("PROVIDER_CONFIG_SYNC_BROADCAST_TOKEN")
    configure(
        provider_records=lambda: providers,
        project_records=lambda: projects,
        sync_home=lambda: sync_home,
        change_webhook_url=change_webhook_url,
        change_webhook_token=change_webhook_token,
    )


def _asset_text(name: str) -> str:
    return files(__package__).joinpath("static", name).read_text(encoding="utf-8")


def standalone_app_html() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Provider Config Sync</title>
  <link rel="stylesheet" href="/assets/standalone-app.css">
</head>
<body>
  <div id="root"></div>
  <script src="/assets/standalone-app.js"></script>
</body>
</html>"""
    )


def standalone_app_js() -> Response:
    return Response(_asset_text("standalone-app.js"), media_type="text/javascript")


def standalone_app_css() -> Response:
    return Response(_asset_text("standalone-app.css"), media_type="text/css")


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("PROVIDER_CONFIG_SYNC_HOST") or "127.0.0.1"
    port = int(os.environ.get("PROVIDER_CONFIG_SYNC_PORT") or "8765")
    uvicorn.run("provider_config_sync_backend.standalone:app", host=host, port=port)


if __name__ == "__main__":
    main()

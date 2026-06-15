from __future__ import annotations

from importlib.resources import files

MCP_APP_URI = "ui://provider-config-sync/main"
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"


def _asset_text(name: str) -> str:
    return files(__package__).joinpath("static", name).read_text(encoding="utf-8")


def _inline_script(source: str) -> str:
    return source.replace("</script", "<\\/script")


def _inline_style(source: str) -> str:
    return source.replace("</style", "<\\/style")


def mcp_app_html() -> str:
    css = _inline_style(_asset_text("mcp-app.css"))
    js = _inline_script(_asset_text("mcp-app.js"))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Provider Config Sync</title>
  <style>{css}</style>
</head>
<body>
  <div id="root"></div>
  <script>{js}</script>
</body>
</html>"""

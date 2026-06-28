"""Build #4/#6: the PCS MCP server registers the capability management tools
(list/load/release) and the core-call helper fails closed without a session.

Run:
    .venv/bin/python provider-config-sync/tests/test_capability_management_tools.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_SRC = _ROOT / "packages" / "provider-config-sync-backend" / "src"
if str(_PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_SRC))

from provider_config_sync_backend.mcp_server import (  # noqa: E402
    _session_capabilities_request,
    create_server,
)

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        FAILURES.append(msg)


def t_management_tools_registered() -> None:
    server = create_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    for needed in ("list_capabilities", "load_capability", "release_capability"):
        check(needed in names, f"tool registered: {needed}")


def t_request_fails_closed_without_session() -> None:
    for prefix in ("BETTER_CLAUDE_", "BETTER_AGENT_"):
        os.environ.pop(prefix + "APP_SESSION_ID", None)
    try:
        _session_capabilities_request("GET")
    except ValueError as error:
        check("no active session" in str(error), "GET without session raises clean error")
    else:
        check(False, "expected ValueError when no session")


def t_request_fails_closed_without_token() -> None:
    os.environ["BETTER_CLAUDE_APP_SESSION_ID"] = "sess-1"
    for prefix in ("BETTER_CLAUDE_", "BETTER_AGENT_"):
        os.environ.pop(prefix + "INTERNAL_TOKEN", None)
    try:
        _session_capabilities_request("GET")
    except ValueError as error:
        check("backend auth" in str(error), "GET without internal token raises clean error")
    else:
        check(False, "expected ValueError when no internal token")
    finally:
        os.environ.pop("BETTER_CLAUDE_APP_SESSION_ID", None)


if __name__ == "__main__":
    t_management_tools_registered()
    t_request_fails_closed_without_session()
    t_request_fails_closed_without_token()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S)")
        raise SystemExit(1)
    print("\nOK")

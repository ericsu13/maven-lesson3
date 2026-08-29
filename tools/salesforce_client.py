"""MCP client wrapper for the Salesforce Account Plan server.

The backend calls submit_via_mcp(); it reaches the create_account_plan tool
over one of two transports (config.MCP_TRANSPORT):

- "stdio" (default): spawn mcp_server.py per call over stdin/stdout.
- "http": connect to a long-running server at config.MCP_HTTP_URL that you
  started by hand, so every request hits that one process.

Either way it returns the tool's {status, message, details} result, keeping
Agent 5 behind the MCP boundary instead of a direct in-process call.
"""

import json
import sys

import anyio
from mcp import Client, StdioServerParameters

from applog import logger
from config import MCP_HTTP_URL, MCP_TRANSPORT

# stdio: launch the MCP server with this project's Python, from the project
# root (inherited cwd) so it can load .env.demo and import the tools package.
_STDIO_SERVER = StdioServerParameters(command=sys.executable, args=["mcp_server.py"])


def _resolve_transport(transport):
    """Fall back to the configured default when no per-request override is given."""
    return (transport or MCP_TRANSPORT or "stdio").lower()


def _connect_target(transport):
    """Return (client_arg, human_label) for the given transport."""
    if transport == "http":
        return MCP_HTTP_URL, f"HTTP server at {MCP_HTTP_URL}"
    return _STDIO_SERVER, f"stdio subprocess '{_STDIO_SERVER.command} {' '.join(_STDIO_SERVER.args)}'"


async def _acall(company: str, plan: dict, transport: str) -> dict:
    target, label = _connect_target(transport)
    if transport == "http":
        logger.info("[MCP CLIENT] Connecting to %s...", label)
    else:
        logger.info("[MCP CLIENT] Launching %s...", label)
    async with Client(target) as client:
        logger.info("[MCP CLIENT] Connected. Invoking MCP tool 'create_account_plan'.")
        logger.info(
            "[MCP CLIENT] Passing competitive analysis JSON for '%s' (%d fields).",
            company,
            len(plan or {}),
        )
        result = await client.call_tool(
            "create_account_plan", {"company": company, "plan": plan}
        )
        text = result.content[0].text if result.content else ""
        if result.is_error:
            logger.warning("[MCP CLIENT] MCP tool reported an error: %s", text)
            return {"status": "FAIL", "message": f"MCP tool error: {text}", "details": {}}
        logger.info("[MCP CLIENT] Received response from MCP tool.")
        try:
            parsed = json.loads(text)
            logger.info("[MCP CLIENT] Parsed result: status=%s.", parsed.get("status"))
            return parsed
        except (ValueError, TypeError):
            logger.warning("[MCP CLIENT] Could not parse MCP response: %s", text)
            return {
                "status": "FAIL",
                "message": f"Unexpected MCP response: {text}",
                "details": {},
            }


def submit_via_mcp(company: str, plan: dict, transport: str = None) -> dict:
    """Blocking wrapper (the pipeline is synchronous): run the async MCP call.

    `transport` overrides config.MCP_TRANSPORT for this call ("stdio"|"http").
    """
    transport = _resolve_transport(transport)
    logger.info(
        "[MCP CLIENT] submit_via_mcp: routing '%s' account plan through MCP (%s transport).",
        company,
        transport,
    )
    try:
        return anyio.run(_acall, company, plan, transport)
    except Exception as exc:
        logger.warning("[MCP CLIENT] call failed: %s", exc)
        hint = ""
        if transport == "http":
            hint = (
                f" Is the HTTP MCP server running at {MCP_HTTP_URL}? "
                "Start it with: mcp_transport=http .venv/bin/python mcp_server.py"
            )
        return {"status": "FAIL", "message": f"MCP invocation failed: {exc}.{hint}", "details": {}}


async def _aping(transport: str) -> None:
    """Open a client and list tools — proves the transport is actually reachable."""
    target, _ = _connect_target(transport)
    async with Client(target) as client:
        await client.list_tools()


def check_mcp_health(transport: str = None) -> dict:
    """Preflight: is the MCP server reachable for the given transport?

    stdio always self-launches, so it's reported healthy without spawning.
    http is genuinely probed (connect + list_tools). Returns
    {ok, transport, detail}.
    """
    transport = _resolve_transport(transport)
    if transport != "http":
        return {"ok": True, "transport": transport, "detail": "stdio launches on demand."}
    try:
        anyio.run(_aping, transport)
        return {"ok": True, "transport": "http", "detail": f"Reachable at {MCP_HTTP_URL}."}
    except Exception as exc:
        logger.info("[MCP CLIENT] health check failed for http: %s", exc)
        return {
            "ok": False,
            "transport": "http",
            "detail": (
                f"No MCP server reachable at {MCP_HTTP_URL}. Start it with: "
                "mcp_transport=http .venv/bin/python mcp_server.py"
            ),
        }

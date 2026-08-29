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


def _connect_target():
    """Return (client_arg, human_label) for the configured transport."""
    if MCP_TRANSPORT == "http":
        return MCP_HTTP_URL, f"HTTP server at {MCP_HTTP_URL}"
    return _STDIO_SERVER, f"stdio subprocess '{_STDIO_SERVER.command} {' '.join(_STDIO_SERVER.args)}'"


async def _acall(company: str, plan: dict) -> dict:
    target, label = _connect_target()
    if MCP_TRANSPORT == "http":
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


def submit_via_mcp(company: str, plan: dict) -> dict:
    """Blocking wrapper (the pipeline is synchronous): run the async MCP call."""
    logger.info(
        "[MCP CLIENT] submit_via_mcp: routing '%s' account plan through MCP (%s transport).",
        company,
        MCP_TRANSPORT,
    )
    try:
        return anyio.run(_acall, company, plan)
    except Exception as exc:
        logger.warning("[MCP CLIENT] call failed: %s", exc)
        return {"status": "FAIL", "message": f"MCP invocation failed: {exc}", "details": {}}

"""MCP client wrapper for the Salesforce Account Plan server.

The backend calls submit_via_mcp(); it launches mcp_server.py over stdio,
invokes the `create_account_plan` tool, and returns its {status, message,
details} result. This keeps Agent 5 behind the MCP boundary instead of a
direct in-process function call.
"""

import json
import sys

import anyio
from mcp import Client, StdioServerParameters

from applog import logger

# Launch the MCP server with this project's Python, from the project root
# (inherited cwd) so it can load .env.demo and import the tools package.
_SERVER = StdioServerParameters(command=sys.executable, args=["mcp_server.py"])


async def _acall(company: str, plan: dict) -> dict:
    logger.info("[MCP CLIENT] Launching MCP server '%s %s' over stdio...", _SERVER.command, " ".join(_SERVER.args))
    async with Client(_SERVER) as client:
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
    logger.info("[MCP CLIENT] submit_via_mcp: routing '%s' account plan through MCP.", company)
    try:
        return anyio.run(_acall, company, plan)
    except Exception as exc:
        logger.warning("[MCP CLIENT] call failed: %s", exc)
        return {"status": "FAIL", "message": f"MCP invocation failed: {exc}", "details": {}}

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
    async with Client(_SERVER) as client:
        result = await client.call_tool(
            "create_account_plan", {"company": company, "plan": plan}
        )
        text = result.content[0].text if result.content else ""
        if result.is_error:
            return {"status": "FAIL", "message": f"MCP tool error: {text}", "details": {}}
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return {
                "status": "FAIL",
                "message": f"Unexpected MCP response: {text}",
                "details": {},
            }


def submit_via_mcp(company: str, plan: dict) -> dict:
    """Blocking wrapper (the pipeline is synchronous): run the async MCP call."""
    logger.info("MCP: calling salesforce-account-plan.create_account_plan for '%s'.", company)
    try:
        return anyio.run(_acall, company, plan)
    except Exception as exc:
        logger.warning("MCP: call failed: %s", exc)
        return {"status": "FAIL", "message": f"MCP invocation failed: {exc}", "details": {}}

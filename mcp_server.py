"""MCP server exposing Agent 5 (the Salesforce write) as an MCP tool.

This runs as a separate process, in one of two transports (config.MCP_TRANSPORT):

- "stdio" (default): the backend's MCP client launches this on demand over
  stdin/stdout. A hand-started copy is NOT used by the app.
- "http": run this once as a long-running server; the client connects to its
  URL, so every request hits this one process and logs to this window. Good
  for demos/debugging.

Run standalone:
    .venv/bin/python mcp_server.py            # uses config.MCP_TRANSPORT
    mcp_transport=http .venv/bin/python mcp_server.py
"""

import logging
import sys

# The MCP stdio protocol owns stdout for its JSON-RPC messages, so redirect our
# console logging (which applog points at stdout) to stderr to avoid corruption.
from applog import logger

for _handler in logger.handlers:
    if isinstance(_handler, logging.StreamHandler) and getattr(_handler, "stream", None) is sys.stdout:
        _handler.stream = sys.stderr

from mcp.server.mcpserver import MCPServer

from config import MCP_HTTP_HOST, MCP_HTTP_PATH, MCP_HTTP_PORT, MCP_HTTP_URL, MCP_TRANSPORT
from tools.salesforce import create_account_plan_record

mcp = MCPServer(
    name="salesforce-account-plan",
    instructions="Writes competitor-analysis account plans into a Salesforce org.",
)


@mcp.tool(
    description=(
        "Find or create the Salesforce Account for `company`, then create an "
        "AccountPlan record from the given 11-field account `plan`. Returns "
        "{status, message, details}."
    )
)
def create_account_plan(company: str, plan: dict) -> dict:
    """Agent 5 as an MCP tool. `plan` is the 11-field account-plan dict."""
    logger.info("=" * 60)
    logger.info("[MCP SERVER] Tool 'create_account_plan' invoked.")
    logger.info("[MCP SERVER] Received company: '%s'", company)
    logger.info(
        "[MCP SERVER] Received competitive analysis JSON: %d fields (%s).",
        len(plan or {}),
        ", ".join((plan or {}).keys()) or "none",
    )
    logger.info("[MCP SERVER] Handing off to Agent 5 (Salesforce write)...")

    status, message, details = create_account_plan_record(company, plan)

    logger.info("[MCP SERVER] Agent 5 returned status=%s: %s", status, message)
    logger.info("[MCP SERVER] Returning result to MCP client.")
    logger.info("=" * 60)
    return {"status": status, "message": message, "details": details}


if __name__ == "__main__":
    if MCP_TRANSPORT == "http":
        logger.info(
            "[MCP SERVER] salesforce-account-plan server starting (streamable-http transport)."
        )
        logger.info("[MCP SERVER] Listening on %s — leave this running.", MCP_HTTP_URL)
        logger.info("[MCP SERVER] Every submit will hit this process. Ctrl-C to stop.")
        mcp.run(
            transport="streamable-http",
            host=MCP_HTTP_HOST,
            port=MCP_HTTP_PORT,
            streamable_http_path=MCP_HTTP_PATH,
        )
    else:
        logger.info("[MCP SERVER] salesforce-account-plan server starting (stdio transport).")
        logger.info("[MCP SERVER] Waiting for an MCP client to connect on stdin/stdout...")
        logger.info(
            "[MCP SERVER] NOTE: in stdio mode the app spawns its own copy per call; "
            "this hand-started process will stay idle. Use mcp_transport=http to be the "
            "server the app connects to."
        )
        mcp.run(transport="stdio")

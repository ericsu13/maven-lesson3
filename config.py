"""Central configuration for the Competitive Analysis Agent system.

Loads credentials from .env.demo and exposes shared settings.
"""

import os

from dotenv import load_dotenv

# Secrets live in .env.demo (git-ignored).
load_dotenv(".env.demo")

# --- API keys (names match the .env.demo file) ---
OPENAI_API_KEY = os.getenv("openAI_key")
PINECONE_API_KEY = os.getenv("pinecone_api_key")
PINECONE_INDEX_NAME = os.getenv("pinecone_index_name")
YOU_API_KEY = os.getenv("youdotcom_key")
# mem0 — accept a few likely key names since the .env naming is inconsistent.
MEM0_API_KEY = (
    os.getenv("mem0_key")
    or os.getenv("MEM0_API_KEY")
    or os.getenv("mem0_api_key")
)

# --- Salesforce demo org (Agent 5) ---
SF_USERNAME = os.getenv("sf_username")
SF_PASSWORD = os.getenv("sf_password")
SF_SECURITY_TOKEN = os.getenv("sf_security_token") or ""
SF_DOMAIN = os.getenv("sf_domain") or "login"
# The standard AccountPlan object requires API v62.0+; use a recent version.
SF_API_VERSION = os.getenv("sf_api_version") or "64.0"

# --- Agent 5 MCP transport ---
# "stdio" (default): the client spawns mcp_server.py per call over stdin/stdout.
# "http": the client connects to a long-running server you start by hand, so
#   you can watch requests hit that one process (better for demos/debugging).
MCP_TRANSPORT = (os.getenv("mcp_transport") or "stdio").lower()
MCP_HTTP_HOST = os.getenv("mcp_http_host") or "127.0.0.1"
# Not 8000 — that's the FastAPI backend. Use a distinct port for the MCP server.
MCP_HTTP_PORT = int(os.getenv("mcp_http_port") or "8765")
MCP_HTTP_PATH = os.getenv("mcp_http_path") or "/mcp"
MCP_HTTP_URL = f"http://{MCP_HTTP_HOST}:{MCP_HTTP_PORT}{MCP_HTTP_PATH}"

# --- Model ---
MODEL = "gpt-5.5"

# --- Behavior ---
# When True, tools print their raw responses so we can verify them.
DEBUG = True

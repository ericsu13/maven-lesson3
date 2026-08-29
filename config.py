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

# --- Model ---
MODEL = "gpt-5.5"

# --- Behavior ---
# When True, tools print their raw responses so we can verify them.
DEBUG = True

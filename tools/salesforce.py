"""Agent 5: write the account plan into a Salesforce demo org.

Reads the latest account plan from mem0 and persists it to the standard
Account Plan object:

1. Look up the Account by Company Name.
2. Create the Account if it doesn't exist.
3. Create an AccountPlan record ("<Company> 2026 Account Plan",
   1/1/2026 - 12/31/2026, Status=Active), mapping the plan's 11 JSON
   sections onto the AccountPlan object's dedicated fields.

Auth uses username/password (SOAP) from .env.demo. The AccountPlan object
is only visible on API v62.0+, so the client pins a recent version.
"""

from datetime import datetime

from simple_salesforce import Salesforce

from applog import logger
from config import (
    SF_API_VERSION,
    SF_DOMAIN,
    SF_PASSWORD,
    SF_SECURITY_TOKEN,
    SF_USERNAME,
)
from tools.memory import read_latest_account_plan

ACCOUNT_PLAN_SOBJECT = "AccountPlan"
PLAN_START_DATE = "2026-01-01"
PLAN_END_DATE = "2026-12-31"
PLAN_STATUS = "Active"

# Plan JSON key -> AccountPlan field API name (verified against the live schema).
# SWOT maps to the Relationship* fields; competitive/strategic to the Account* fields.
PLAN_FIELD_MAP = {
    "strengths": "RelationshipStrengths",
    "weaknesses": "RelationshipWeaknesses",
    "opportunities": "RelationshipOpportunities",
    "threats": "RelationshipThreats",
    "strategic_priorities": "AccountStrategicPriorities",
    "challenges": "AccountChallenges",
    "kpis": "AccountPrfmIndicators",
    "industry_trends": "AccountIndustryTrends",
    "competitive_strengths": "AccountCompetitiveStrengths",
    "competitive_weaknesses": "AccountCmptvWeaknesses",
    "competitors": "AccountCompetitors",
}

_sf = None


def _connect():
    """Lazily open a Salesforce session (SOAP username/password)."""
    global _sf
    if _sf is None:
        if not SF_USERNAME or not SF_PASSWORD:
            raise RuntimeError("Salesforce credentials missing from .env.demo.")
        _sf = Salesforce(
            username=SF_USERNAME,
            password=SF_PASSWORD,
            security_token=SF_SECURITY_TOKEN,
            domain=SF_DOMAIN,
            version=SF_API_VERSION,
        )
    return _sf


def _soql_escape(value: str) -> str:
    """Escape single quotes/backslashes for a SOQL string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_or_create_account(sf, company: str) -> str:
    """Return the Id of the Account named `company`, creating it if needed."""
    safe = _soql_escape(company)
    res = sf.query(f"SELECT Id FROM Account WHERE Name = '{safe}' LIMIT 1")
    records = res.get("records", [])
    if records:
        account_id = records[0]["Id"]
        logger.info("Agent 5: found existing Account '%s' (%s).", company, account_id)
        return account_id

    created = sf.Account.create({"Name": company})
    account_id = created["id"]
    logger.info("Agent 5: created Account '%s' (%s).", company, account_id)
    return account_id


def _render_value(value) -> str:
    """Render a plan value (usually list[str]) as readable bullet text."""
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return str(value)


def _build_record(sf, company: str, account_id: str, plan: dict) -> tuple[dict, list]:
    """Assemble the AccountPlan field values, skipping any field the org lacks.

    Returns (record, skipped_keys). Field existence is checked against a live
    describe so the write degrades gracefully if the org's schema differs.
    """
    available = {f["name"] for f in getattr(sf, ACCOUNT_PLAN_SOBJECT).describe()["fields"]}

    record = {
        "Name": f"{company} 2026 Account Plan",
        "AccountId": account_id,
        "StartDate": PLAN_START_DATE,
        "EndDate": PLAN_END_DATE,
        "Status": PLAN_STATUS,
    }
    # The object has no visible "last updated" field, so stamp provenance
    # into Notes (local time; the user's machine is Pacific).
    if "Notes" in available:
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        record["Notes"] = f"Updated {stamp} by LangChain agent"

    skipped = []
    for key, value in plan.items():
        field = PLAN_FIELD_MAP.get(key)
        if field and field in available:
            record[field] = _render_value(value)[:32000]
        else:
            skipped.append(key)

    # Drop any fixed field the org doesn't actually expose.
    record = {k: v for k, v in record.items() if k in available or k == "Name"}
    return record, skipped


def create_account_plan_record(company: str, plan: dict):
    """Create the Account (if needed) and the AccountPlan record for `plan`.

    `plan` is the 11-field dict (as edited by the human-in-the-loop UI).
    Returns (status, message, details) where details carries the created ids.
    """
    if not company or not plan:
        return "FAIL", "No company or account plan provided.", {}

    try:
        sf = _connect()
    except Exception as exc:
        return "FAIL", f"Salesforce login failed: {exc}", {}

    try:
        account_id = find_or_create_account(sf, company)
        record, skipped = _build_record(sf, company, account_id, plan)
        if skipped:
            logger.warning("Agent 5: no AccountPlan field for keys: %s", skipped)

        logger.info(
            "Agent 5: creating AccountPlan '%s' with %d fields.",
            record["Name"],
            len(record),
        )
        created = getattr(sf, ACCOUNT_PLAN_SOBJECT).create(record)
        plan_id = created["id"]
        logger.info("Agent 5: created AccountPlan %s for '%s'.", plan_id, company)
        instance = getattr(sf, "sf_instance", "")
        details = {
            "account_id": account_id,
            "account_plan_id": plan_id,
            "record_url": f"https://{instance}/{plan_id}" if instance else "",
            "name": record["Name"],
        }
        return "PASS", (
            f"Created AccountPlan '{record['Name']}' ({plan_id}) "
            f"on Account {account_id}."
        ), details
    except Exception as exc:
        logger.warning("Agent 5: Salesforce write failed: %s", exc)
        return "FAIL", f"Salesforce write failed: {exc}", {}


def write_account_plan_to_salesforce():
    """Read the latest account plan from mem0 and create the SF records (CLI path).

    Returns (status, message).
    """
    company, plan = read_latest_account_plan()
    if not company or not plan:
        return "FAIL", "No account plan found in mem0 to write to Salesforce."
    status, message, _ = create_account_plan_record(company, plan)
    return status, message

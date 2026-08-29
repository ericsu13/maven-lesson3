"""you.com Search API tools for the agent pipeline.

- `web_search`: a LangChain tool wrapping a single you.com query.
- `discover_competitors` (Agent 1): finds up to 3 competitor names via Comparably.
- `research_competitor` (Agent 2): deep competitor intel — news, product, market
  share, growth.
- `research_company` (Agent 3): internal research on the primary company — news,
  earnings, market positioning.
"""

import re

import requests
from langchain_core.tools import tool

from applog import logger
from config import DEBUG, YOU_API_KEY

# you.com Search API
YOU_SEARCH_ENDPOINT = "https://api.you.com/v1/search"

# Up to 3 competitors.
MAX_COMPETITORS = 3

# Top N results kept per topic query when gathering intel.
PER_TOPIC_RESULTS = 2

# Agent 2 — depth dimensions for each competitor.
COMPETITOR_TOPICS = [
    "latest news and announcements 2026",
    "product overview and key features",
    "market share revenue and growth",
]

# Agent 3 — internal research dimensions for the primary company.
COMPANY_TOPICS = [
    "latest news and announcements 2026",
    "earnings and financial results",
    "market positioning and strategy",
]


def _you_search(web_request: str):
    """Call you.com and return (results, issues).

    results: raw list of web result dicts ([] on failure).
    issues:  list of human-readable problems (empty on success).
    """
    issues = []

    if not YOU_API_KEY:
        issues.append("Missing you.com API key (youdotcom_key) in .env.demo")
        return [], issues

    if not web_request or not web_request.strip():
        issues.append("Empty web_request — nothing to search for")
        return [], issues

    try:
        response = requests.get(
            YOU_SEARCH_ENDPOINT,
            params={"query": web_request},
            headers={"X-API-Key": YOU_API_KEY},
            timeout=30,
        )
    except requests.RequestException as exc:
        issues.append(f"Request to you.com failed: {exc}")
        return [], issues

    if response.status_code != 200:
        issues.append(
            f"you.com returned HTTP {response.status_code}: {response.text[:200]}"
        )
        return [], issues

    try:
        data = response.json()
    except ValueError:
        issues.append("you.com response was not valid JSON")
        return [], issues

    results = (data.get("results") or {}).get("web") or []
    if not results:
        issues.append("No results returned for the query")
        return [], issues

    return results, issues


def _sides_match(company: str, side: str) -> bool:
    """True if a `vs` title side refers to our company."""
    company, side = company.lower().strip(), side.lower().strip()
    if not company or not side:
        return False
    return company == side or company in side or side in company


def discover_competitors(company: str, limit: int = MAX_COMPETITORS):
    """Return (status, names, message): up to `limit` competitor names.

    status:  "PASS" or "FAIL".
    names:   list of competitor names ([] on FAIL).
    message: human-readable summary / failure reason.

    Comparably publishes "<Company> vs <Competitor>" comparison pages. Their
    result titles carry clean, proper-cased company names, so we parse the
    competitor from each title that features our company.
    """
    company = (company or "").strip()
    if not company:
        return "FAIL", [], "No company name provided."

    web_request = f"{company} competitors site:comparably.com"
    results, issues = _you_search(web_request)
    if issues:
        return "FAIL", [], f"Search failed for '{company}': {issues[0]}"

    names = []
    for result in results:
        # Titles look like "Salesforce vs SAP | Comparably".
        title = (result.get("title") or "").split("|")[0].strip()
        match = re.match(r"(.+?)\s+vs\s+(.+)", title, re.IGNORECASE)
        if not match:
            continue
        left, right = match.group(1).strip(), match.group(2).strip()
        # Require our company on one side; the other side is the competitor.
        if not (_sides_match(company, left) or _sides_match(company, right)):
            continue
        competitor = right if _sides_match(company, left) else left
        if not _sides_match(company, competitor) and competitor not in names:
            names.append(competitor)

    names = names[:limit]

    logger.info("Agent 1 (discover): '%s' -> competitors: %s", company, names)

    if not names:
        return (
            "FAIL",
            [],
            f"Company '{company}' not found on Comparably, or it has no "
            "listed competitors.",
        )

    return "PASS", names, f"Found {len(names)} competitor(s) for '{company}'."


def _gather_intel(entity: str, topics: list, per_topic: int = PER_TOPIC_RESULTS):
    """Search `entity` across several topics; return (items, issues).

    items: deduped list of {topic, title, url, snippet}.
    """
    items, issues = [], []
    seen_urls = set()

    for topic in topics:
        results, topic_issues = _you_search(f"{entity} {topic}")
        if topic_issues:
            issues.append(f"{topic}: {topic_issues[0]}")
            continue
        for result in results[:per_topic]:
            title = (result.get("title") or "").strip()
            url = (result.get("url") or "").strip()
            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            snippet = (result.get("description") or "").strip()
            if not snippet:
                snippet = " ".join(result.get("snippets") or []).strip()
            items.append(
                {"topic": topic, "title": title, "url": url, "snippet": snippet}
            )

    return items, issues


def _log_items(prefix: str, entity: str, items: list):
    logger.info("%s: '%s' — %d item(s)", prefix, entity, len(items))
    for item in items:
        logger.info("   [%s] %s", item["topic"], item["title"][:80])


def research_competitor(competitor: str):
    """Agent 2: deep intel on one competitor (news, product, market, growth).

    Returns (competitor, status, items, message).
    """
    competitor = (competitor or "").strip()
    if not competitor:
        return competitor, "FAIL", [], "No competitor name provided."

    items, issues = _gather_intel(competitor, COMPETITOR_TOPICS)
    if not items:
        detail = f" ({issues[0]})" if issues else ""
        msg = f"No competitor intel found for '{competitor}'.{detail}"
        logger.warning("Agent 2 (competitor): %s", msg)
        return competitor, "FAIL", [], msg

    _log_items("Agent 2 (competitor)", competitor, items)
    return competitor, "PASS", items, f"Found {len(items)} item(s) for '{competitor}'."


def research_company(company: str):
    """Agent 3: internal research on the primary company.

    News, earnings, and market positioning (not about competitors).
    Returns (company, status, items, message).
    """
    company = (company or "").strip()
    if not company:
        return company, "FAIL", [], "No company name provided."

    items, issues = _gather_intel(company, COMPANY_TOPICS)
    if not items:
        detail = f" ({issues[0]})" if issues else ""
        msg = f"No internal research found for '{company}'.{detail}"
        logger.warning("Agent 3 (internal): %s", msg)
        return company, "FAIL", [], msg

    _log_items("Agent 3 (internal)", company, items)
    return company, "PASS", items, f"Found {len(items)} item(s) for '{company}'."


@tool
def web_search(web_request: str) -> str:
    """Search the web via the you.com Search API for the given web_request.

    Returns "PASS" if the search succeeds, otherwise a text list of issues.
    """
    results, issues = _you_search(web_request)

    if DEBUG:
        web_response = "\n".join(
            f"- {(r.get('title') or '').strip()} | {(r.get('url') or '').strip()}"
            for r in results
        )
        print("\n[DEBUG] web_request:", web_request)
        print("[DEBUG] web_response:\n" + (web_response or "<empty>"))

    if issues:
        return "Issues:\n- " + "\n- ".join(issues)
    return "PASS"

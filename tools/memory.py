"""mem0 memory tools for the orchestrator.

Two stores, both written verbatim (infer=False) for full detail:
- `competitive_news`   — Agent 2 intel about each competitor.
- `internal_research`  — Agent 3 intel about the primary company.

We never delete. mem0's bulk delete is a slow async job that races (and
silently wipes) fresh writes. Instead, every memory is stamped with a
`run_ts` (epoch seconds); reads filter to the most recent run_ts, so old
runs sit harmlessly and are never read.
"""

import json
import time

from applog import logger
from config import MEM0_API_KEY

# Namespace so all runs of this system group together in mem0.
MEM0_USER_ID = "competitive-analysis"
COMPETITIVE_CATEGORY = "competitive_news"
INTERNAL_CATEGORY = "internal_research"
ACCOUNT_PLAN_CATEGORY = "account_plan"

# infer=False stores research verbatim (full detail) and synchronously.
INFER = False

_client = None


def new_run_ts() -> int:
    """A per-run timestamp (epoch seconds) to stamp this run's memories."""
    return int(time.time())


def _get_client():
    """Lazily construct the mem0 client (so import never fails on a missing key)."""
    global _client
    if _client is None:
        from mem0 import MemoryClient

        _client = MemoryClient(api_key=MEM0_API_KEY)
    return _client


def _all_memories():
    res = _get_client().get_all(filters={"user_id": MEM0_USER_ID}, page_size=100)
    return res.get("results", res) if isinstance(res, dict) else (res or [])


def _run_ts_of(memory) -> int:
    """Extract run_ts from a memory's metadata (0 if absent/unparseable)."""
    raw = (memory.get("metadata") or {}).get("run_ts")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def items_to_content(headline: str, items: list) -> str:
    """Render intel items as natural-language text."""
    lines = [headline]
    for item in items:
        topic = item.get("topic", "").strip()
        title = item.get("title", "").strip()
        snippet = item.get("snippet", "").strip()
        url = item.get("url", "").strip()
        line = f"- ({topic}) {title}" if topic else f"- {title}"
        if snippet:
            line += f": {snippet}"
        if url:
            line += f" ({url})"
        lines.append(line)
    return "\n".join(lines)


def _add_memory(client, content: str, metadata: dict):
    client.add(
        messages=[{"role": "user", "content": content}],
        user_id=MEM0_USER_ID,
        metadata=metadata,
        infer=INFER,
    )


def write_competitive_news(company: str, competitive_news: list, run_ts: int):
    """Write the competitive_news array to mem0 (one memory per competitor).

    `competitive_news` is a list of {"competitor", "items"} entries.
    Every memory is stamped with `run_ts`. Returns (status, message).
    """
    if not MEM0_API_KEY:
        return "FAIL", "mem0 API key not found in .env.demo (expected 'mem0_key')."
    if not competitive_news:
        return "FAIL", "No competitive_news to write."

    try:
        client = _get_client()
    except Exception as exc:
        return "FAIL", f"Could not initialize mem0 client: {exc}"

    written = 0
    for entry in competitive_news:
        competitor = entry.get("competitor")
        items = entry.get("items") or []
        if not competitor or not items:
            continue
        content = items_to_content(
            f"Competitive intel about {competitor} (a competitor of {company}):",
            items,
        )
        try:
            _add_memory(
                client,
                content,
                {
                    "category": COMPETITIVE_CATEGORY,
                    "company": company,
                    "competitor": competitor,
                    "run_ts": run_ts,
                },
            )
            written += 1
            logger.info("Memory: wrote competitive_news for '%s'.", competitor)
        except Exception as exc:
            return "FAIL", f"mem0 write failed for '{competitor}': {exc}"

    if written == 0:
        return "FAIL", "No competitor entries had items to write."

    return "PASS", f"Wrote competitive_news for {written} competitor(s) to mem0."


def write_internal_research(company: str, items: list, run_ts: int):
    """Write Agent 3's internal research on the primary company to mem0.

    Stamped with `run_ts`. Returns (status, message).
    """
    if not MEM0_API_KEY:
        return "FAIL", "mem0 API key not found in .env.demo (expected 'mem0_key')."
    if not items:
        return "FAIL", "No internal research to write."

    try:
        client = _get_client()
    except Exception as exc:
        return "FAIL", f"Could not initialize mem0 client: {exc}"

    content = items_to_content(f"Internal research about {company}:", items)
    try:
        _add_memory(
            client,
            content,
            {"category": INTERNAL_CATEGORY, "company": company, "run_ts": run_ts},
        )
    except Exception as exc:
        return "FAIL", f"mem0 write failed for '{company}': {exc}"

    logger.info("Memory: wrote internal_research for '%s'.", company)
    return "PASS", f"Wrote internal_research for '{company}' to mem0."


def write_account_plan(company: str, plan: dict, run_ts: int):
    """Write Agent 4's structured account plan to mem0 (verbatim JSON).

    Stored under its own category with the same run_ts, so it lives alongside
    this run's research and is returned by read_latest_run(). Returns
    (status, message).
    """
    if not MEM0_API_KEY:
        return "FAIL", "mem0 API key not found in .env.demo (expected 'mem0_key')."
    if not plan:
        return "FAIL", "No account plan to write."

    try:
        client = _get_client()
        _add_memory(
            client,
            json.dumps(plan, ensure_ascii=False),
            {
                "category": ACCOUNT_PLAN_CATEGORY,
                "company": company,
                "run_ts": run_ts,
            },
        )
    except Exception as exc:
        return "FAIL", f"mem0 write failed for account plan: {exc}"

    logger.info("Memory: wrote account_plan for '%s'.", company)
    return "PASS", f"Wrote account_plan for '{company}' to mem0."


def read_latest_account_plan():
    """Return (company, plan_dict) from the latest run, or (None, None)."""
    _, memories = read_latest_run()
    for memory in memories:
        md = memory.get("metadata") or {}
        if md.get("category") == ACCOUNT_PLAN_CATEGORY:
            try:
                return md.get("company"), json.loads(memory.get("memory") or "{}")
            except json.JSONDecodeError:
                return md.get("company"), None
    return None, None


def wait_for_run(run_ts: int, expected_min: int, timeout: int = 30, interval: int = 3):
    """Poll until at least `expected_min` memories tagged `run_ts` are visible.

    A consistency guard before the pipeline reads this run's memories back.
    Returns (count, ready).
    """
    if not MEM0_API_KEY:
        return 0, False

    try:
        _get_client()
    except Exception as exc:
        logger.warning("Memory: could not poll mem0: %s", exc)
        return 0, False

    start = time.time()
    count = 0
    while time.time() - start < timeout:
        count = sum(1 for m in _all_memories() if _run_ts_of(m) == run_ts)
        if count >= expected_min:
            logger.info("Memory: run %d ready with %d memories.", run_ts, count)
            return count, True
        time.sleep(interval)

    logger.warning(
        "Memory: run %d only %d/%d memories after %ds.",
        run_ts,
        count,
        expected_min,
        timeout,
    )
    return count, False


def read_run(run_ts: int):
    """Return the memories tagged with exactly `run_ts` (this specific run)."""
    return [m for m in _all_memories() if _run_ts_of(m) == run_ts]


def read_latest_run():
    """Return (run_ts, memories) for the most recent run_ts in the store.

    This is what downstream agents read from — old runs are ignored.
    """
    memories = _all_memories()
    if not memories:
        return None, []
    latest = max(_run_ts_of(m) for m in memories)
    if latest == 0:
        return None, []
    return latest, [m for m in memories if _run_ts_of(m) == latest]


def list_all_memories():
    """Public accessor for every memory in this namespace (all runs)."""
    return _all_memories()


def delete_memories(memories):
    """Delete the given memories by id (prompt, synchronous). Returns count deleted."""
    client = _get_client()
    deleted = 0
    for memory in memories or []:
        memory_id = memory.get("id") or memory.get("memory_id")
        if memory_id:
            client.delete(memory_id)
            deleted += 1
    return deleted

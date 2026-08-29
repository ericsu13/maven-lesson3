"""Reusable orchestration for the Competitive Analysis Agent.

Splits the flow into two steps so a human can review/edit between them:

- analyze(company): Agents 1-4. Discovers competitors, researches them and
  the company in parallel, stores research in mem0, and synthesizes the
  11-field account plan. Returns the plan for review (does NOT write to SF).
- submit(company, plan): Agent 5. Writes the (possibly edited) plan to the
  Salesforce demo org.

Every result is a plain JSON-serializable dict so the web API can return it.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from applog import logger
from tools.account_plan import _partition, synthesize_account_plan
from tools.memory import (
    items_to_content,
    new_run_ts,
    read_run,
    wait_for_run,
    write_account_plan,
    write_competitive_news,
    write_internal_research,
)
from tools.salesforce_client import submit_via_mcp
from tools.web_search import (
    discover_competitors,
    research_company,
    research_competitor,
)


def analyze_stream(company: str, use_memory: bool = True):
    """Run Agents 1-4 for `company`, yielding progress events as it goes.

    When `use_memory` is True (default) the research is written to mem0 and
    read back for synthesis (persistent, matches the original design). When
    False, the research is passed straight to Agent 4 and mem0 is skipped.

    Yields dicts:
      {"type": "step",   "message": "..."}   progress narration
      {"type": "result", company, run_ts, competitors, plan}   final success
      {"type": "error",  stage, message}     user-friendly failure

    The generator is the single source of truth; analyze() just drains it.
    """
    company = (company or "").strip()
    if not company:
        yield {"type": "error", "stage": "input", "message": "Please enter a company name."}
        return

    run_ts = new_run_ts()
    logger.info("Pipeline: analyzing '%s' (run %d).", company, run_ts)

    # --- Agent 1: discover competitors ---
    yield {"type": "step", "message": f"Discovering the top competitors of {company}…"}
    status, names, message = discover_competitors(company)
    if status == "FAIL":
        yield {"type": "error", "stage": "discover", "message": message}
        return
    yield {"type": "step", "message": f"Found {len(names)} competitors: {', '.join(names)}."}

    # --- Agents 2 & 3 in parallel, narrating each as it finishes ---
    yield {
        "type": "step",
        "message": f"Researching {company} and its competitors in parallel…",
    }
    competitor_results = []
    company_result = None
    with ThreadPoolExecutor(max_workers=len(names) + 1) as pool:
        futures = {pool.submit(research_company, company): ("company", company)}
        for name in names:
            futures[pool.submit(research_competitor, name)] = ("competitor", name)

        for future in as_completed(futures):
            kind, label = futures[future]
            result = future.result()
            if kind == "company":
                company_result = result
                yield {
                    "type": "step",
                    "message": f"Gathered internal research on {company} "
                    "(news, earnings, positioning).",
                }
            else:
                competitor_results.append(result)
                yield {"type": "step", "message": f"Gathered competitive intel on {label}."}

    competitive_news = []
    for competitor, c_status, items, c_message in competitor_results:
        if c_status == "PASS":
            competitive_news.append({"competitor": competitor, "items": items})
        else:
            logger.warning("Pipeline: skipped competitor %s: %s", competitor, c_message)

    if not competitive_news:
        yield {
            "type": "error",
            "stage": "research",
            "message": (
                f"Found competitors for {company} but could not gather any "
                "usable intel about them. Please try again."
            ),
        }
        return

    _, company_status, company_items, company_message = company_result

    # The research we just gathered, rendered to text (used directly, or as a
    # fallback if the mem0 read-back hasn't indexed yet).
    local_competitive = [
        items_to_content(
            f"Competitive intel about {entry['competitor']} (a competitor of {company}):",
            entry["items"],
        )
        for entry in competitive_news
    ]
    local_internal = []
    if company_status == "PASS" and company_items:
        local_internal.append(
            items_to_content(f"Internal research about {company}:", company_items)
        )

    if use_memory:
        # --- Persist research to mem0 (stamped with run_ts), then read it back ---
        yield {"type": "step", "message": "Storing research in memory (mem0)…"}
        cn_status, cn_message = write_competitive_news(company, competitive_news, run_ts)
        if cn_status == "FAIL":
            yield {"type": "error", "stage": "memory", "message": cn_message}
            return

        expected = len(competitive_news)
        if company_status == "PASS":
            ir_status, _ = write_internal_research(company, company_items, run_ts)
            if ir_status == "PASS":
                expected += 1
        else:
            logger.warning("Pipeline: no internal research: %s", company_message)

        wait_for_run(run_ts, expected_min=expected)

        # Agent 4 reads this run's research back from mem0. If mem0's async
        # indexing hasn't caught up, fall back to the in-memory research so
        # synthesis never fails on a read-back race.
        yield {"type": "step", "message": "Reading research back from memory (mem0)…"}
        internal, competitive = _partition(read_run(run_ts))
        if not internal and not competitive:
            logger.warning("Pipeline: mem0 read-back empty; using in-memory research.")
            internal, competitive = local_internal, local_competitive
    else:
        # --- Skip mem0: hand the in-memory research straight to Agent 4 ---
        internal, competitive = local_internal, local_competitive

    yield {"type": "step", "message": "Synthesizing the account plan across 11 dimensions…"}
    ap_status, plan, ap_message = synthesize_account_plan(company, internal, competitive)

    if ap_status != "PASS" or plan is None:
        yield {"type": "error", "stage": "synthesis", "message": ap_message}
        return

    plan_dict = plan.model_dump()
    if use_memory:
        write_account_plan(company, plan_dict, run_ts)  # keep mem0 in sync with the run

    yield {"type": "step", "message": "Done. Assembling your account plan for review."}
    yield {
        "type": "result",
        "company": company,
        "run_ts": run_ts,
        "competitors": names,
        "plan": plan_dict,
    }


def analyze(company: str, use_memory: bool = True) -> dict:
    """Non-streaming convenience wrapper: drain analyze_stream to its outcome.

    Returns the {status:"PASS"|"FAIL", ...} dict (as before), for the CLI/tests.
    """
    result = {"status": "FAIL", "stage": "unknown", "message": "No result produced."}
    for event in analyze_stream(company, use_memory=use_memory):
        if event["type"] == "result":
            result = {**event, "status": "PASS"}
            result.pop("type", None)
        elif event["type"] == "error":
            result = {"status": "FAIL", "stage": event.get("stage"), "message": event["message"]}
    return result


def submit(company: str, plan: dict) -> dict:
    """Run Agent 5 via MCP: write the (edited) plan to Salesforce.

    Delegates to the salesforce-account-plan MCP server rather than calling
    the Salesforce tool in-process. Returns a {status, message, details} dict.
    """
    company = (company or "").strip()
    if not company or not plan:
        return {"status": "FAIL", "message": "Missing company or account plan."}

    return submit_via_mcp(company, plan)

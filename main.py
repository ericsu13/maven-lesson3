"""Competitive Analysis Agent — entry point.

Current step: a clean dialogue that asks for a Company Name and runs the
web_search tool against the you.com Search API to find the company's
top 3 competitors.
"""

from concurrent.futures import ThreadPoolExecutor

from applog import logger
from tools.account_plan import build_account_plan
from tools.salesforce import write_account_plan_to_salesforce
from tools.memory import (
    new_run_ts,
    wait_for_run,
    write_account_plan,
    write_competitive_news,
    write_internal_research,
)
from tools.web_search import (
    discover_competitors,
    research_company,
    research_competitor,
)

TITLE = "Competitive Analysis Agent"
WIDTH = 56


def print_header():
    print("=" * WIDTH)
    print(TITLE.center(WIDTH))
    print("=" * WIDTH)


def main():
    print_header()

    company = input("\nEnter a Company Name to research: ").strip()
    if not company:
        print("\nNo company name entered. Exiting.")
        return

    # Stamp this run so reads can isolate the latest run (no deletes needed).
    run_ts = new_run_ts()

    status, names, message = discover_competitors(company)

    print("\n" + "-" * WIDTH)
    if status == "FAIL":
        # Negative condition: no company / no competitors found.
        # Abort the pipeline — do not run the downstream research agents.
        print("FAIL:", message)
        print("-" * WIDTH)
        print("Aborting: no competitor data to research.")
        return

    print(f"Top {len(names)} competitors of {company} (via Comparably):")
    for name in names:
        print("  -", name)
    print("-" * WIDTH)

    # --- Agents 2 & 3 run in parallel ---
    #   Agent 2: deep intel on each competitor.
    #   Agent 3: internal research on the primary company.
    print("\nResearching competitors (Agent 2) and the company (Agent 3)...")
    with ThreadPoolExecutor(max_workers=len(names) + 1) as pool:
        company_future = pool.submit(research_company, company)
        competitor_futures = [pool.submit(research_competitor, n) for n in names]
        competitor_results = [f.result() for f in competitor_futures]
        company_result = company_future.result()

    # Compile Agent 2 output into the competitive_news array.
    competitive_news = []
    for competitor, status, items, message in competitor_results:
        if status == "PASS":
            competitive_news.append({"competitor": competitor, "items": items})
        else:
            print(f"  (skipped competitor {competitor}: {message})")

    if not competitive_news:
        print("\nFAIL: No competitor intel gathered.")
        print("Aborting: nothing to store or analyze.")
        return

    # --- Orchestrator: persist both stores to mem0 (stamped with run_ts) ---
    print("\nWriting to mem0...")
    cn_status, cn_message = write_competitive_news(company, competitive_news, run_ts)
    print(f"  competitive_news -> {cn_status}: {cn_message}")

    expected = len(competitive_news)  # one memory per competitor
    _, company_status, company_items, company_message = company_result
    if company_status == "PASS":
        ir_status, ir_message = write_internal_research(company, company_items, run_ts)
        print(f"  internal_research -> {ir_status}: {ir_message}")
        if ir_status == "PASS":
            expected += 1  # plus the internal-research memory
    else:
        # Agent 3 is supplementary; don't abort the pipeline if it fails.
        logger.warning("Agent 3 produced no internal research: %s", company_message)
        print(f"  internal_research -> SKIP: {company_message}")

    # Confirm this run's memories are visible before anything reads them.
    print("\nConfirming mem0 writes...")
    count, ready = wait_for_run(run_ts, expected_min=expected)
    print(f"  run {run_ts}: {count}/{expected} memories visible (ready={ready}).")

    # --- Agent 4: synthesize the account plan from the latest run's memories ---
    print("\nBuilding account plan (Agent 4)...")
    ap_status, plan, ap_message = build_account_plan(company)
    print(f"  {ap_status}: {ap_message}")
    if ap_status != "PASS":
        print("Aborting: could not build the account plan.")
        return

    # Persist the account plan to mem0 (same run_ts, alongside the research).
    plan_dict = plan.model_dump()
    ap_write_status, ap_write_message = write_account_plan(company, plan_dict, run_ts)
    print(f"  account_plan -> {ap_write_status}: {ap_write_message}")

    # Summarize on screen.
    print("\n" + "=" * WIDTH)
    print(f"ACCOUNT PLAN - {company}".center(WIDTH))
    print("=" * WIDTH)
    for field, values in plan_dict.items():
        print(f"\n{field.replace('_', ' ').title()}:")
        for value in values:
            print(f"  - {value}")
    print("\n" + "=" * WIDTH)

    # --- Agent 5: write the account plan into the Salesforce demo org ---
    print("\nWriting account plan to Salesforce (Agent 5)...")
    sf_status, sf_message = write_account_plan_to_salesforce()
    print(f"  {sf_status}: {sf_message}")


if __name__ == "__main__":
    main()

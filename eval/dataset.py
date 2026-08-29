"""Evaluation dataset for the Competitive Analysis Agent.

Each case exercises the pipeline end to end and declares the behavior we expect,
so the harness can score it deterministically. The axes covered:

- Company reality:  real (has a Comparably page) vs. fictitious (must fail
  gracefully at Agent 1 rather than hallucinate competitors).
- Salesforce presence:  companies whose Account already exists in the demo org
  (Salesforce, Schlumberger) vs. ones that don't (a created-then-cleaned Account).
- Memory:  use_memory on vs. off.
- MCP transport:  stdio (spawned per call) vs. http (long-running server).

`expect` is the contract we assert:
- "plan"          -> analyze must return a plan with all 11 fields populated.
- "discover_fail" -> analyze must fail at the "discover" stage (no competitors),
                     and must NOT produce a plan.

`submit` decides whether Agent 5 writes the plan to Salesforce (only meaningful
for cases that produce a plan). Every record the run creates is cleaned up after.
"""

# The 11 account-plan fields Agent 4 must populate (matches AccountPlan model).
EXPECTED_PLAN_FIELDS = [
    "strengths",
    "weaknesses",
    "opportunities",
    "threats",
    "strategic_priorities",
    "challenges",
    "kpis",
    "industry_trends",
    "competitive_strengths",
    "competitive_weaknesses",
    "competitors",
]

CASES = [
    {
        "id": "salesforce-mem-stdio",
        "company": "Salesforce",
        "use_memory": True,
        "transport": "stdio",
        "submit": True,
        "expect": "plan",
        "sf_expected": "exists",  # Account should already be in the org (protected on cleanup)
        "note": "Real company, exists in SF. Memory on, stdio transport.",
    },
    {
        "id": "schlumberger-nomem-stdio",
        "company": "Schlumberger",
        "use_memory": False,
        "transport": "stdio",
        "submit": True,
        "expect": "plan",
        "sf_expected": "exists",
        "note": "Real company, exists in SF. Memory off (in-memory research), stdio.",
    },
    {
        "id": "databricks-mem-stdio",
        "company": "Databricks",
        "use_memory": True,
        "transport": "stdio",
        "submit": True,
        "expect": "plan",
        "sf_expected": "new",  # Account likely absent -> created, then deleted on cleanup
        "note": "Real company, likely NOT in SF. Exercises Account create + account cleanup.",
    },
    {
        "id": "salesforce-mem-http",
        "company": "Salesforce",
        "use_memory": True,
        "transport": "http",
        "submit": True,
        "expect": "plan",
        "sf_expected": "exists",
        "note": "Same real company over the http MCP transport (server booted by the harness).",
    },
    {
        "id": "zorptech-mem",
        "company": "Zorptech Dynamics",
        "use_memory": True,
        "transport": "stdio",
        "submit": False,
        "expect": "discover_fail",
        "sf_expected": "none",
        "note": "Fictitious company. Must fail gracefully at Agent 1, not invent competitors.",
    },
    {
        "id": "fybernetic-nomem",
        "company": "Fybernetic Global Solutions",
        "use_memory": False,
        "transport": "stdio",
        "submit": False,
        "expect": "discover_fail",
        "sf_expected": "none",
        "note": "Fictitious company, memory off. Must fail gracefully at discovery.",
    },
]

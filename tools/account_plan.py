"""Agent 4: synthesize the latest run's mem0 research into an account plan.

Reads the most recent run's memories (internal research on the primary
company + competitive intel), then uses gpt-5.5 with structured output to
fill the account-plan fields.
"""

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from applog import logger
from config import MODEL, OPENAI_API_KEY
from tools.memory import COMPETITIVE_CATEGORY, INTERNAL_CATEGORY, read_latest_run


class AccountPlan(BaseModel):
    """Structured competitor-analysis account plan for the primary company."""

    strengths: list[str] = Field(description="The company's internal strengths.")
    weaknesses: list[str] = Field(description="The company's internal weaknesses.")
    opportunities: list[str] = Field(description="External opportunities to pursue.")
    threats: list[str] = Field(description="External threats to the company.")
    strategic_priorities: list[str] = Field(
        description="Top strategic priorities the company should focus on."
    )
    challenges: list[str] = Field(description="Key challenges the company faces.")
    kpis: list[str] = Field(
        description="Key performance indicators to track for this account."
    )
    industry_trends: list[str] = Field(
        description="Relevant industry trends shaping the market."
    )
    competitive_strengths: list[str] = Field(
        description="Where the company is stronger than its competitors."
    )
    competitive_weaknesses: list[str] = Field(
        description="Where competitors are stronger, or the company lags."
    )
    competitors: list[str] = Field(
        description="Named competitors, each with a brief positioning note."
    )


SYSTEM_PROMPT = (
    "You are a strategic account analyst. Using ONLY the research provided, "
    "produce a concise competitor-analysis account plan for the primary "
    "company. Ground every point in the research; do not invent facts. "
    "Distinguish the company's own SWOT from its competitive position "
    "(competitive_strengths/weaknesses = how it stacks up against the named "
    "competitors). Provide 3-6 concise bullet points per field."
)


def _partition(memories):
    """Split memories into (internal_texts, competitor_texts)."""
    internal, competitive = [], []
    for memory in memories:
        category = (memory.get("metadata") or {}).get("category")
        text = memory.get("memory") or ""
        if category == INTERNAL_CATEGORY:
            internal.append(text)
        elif category == COMPETITIVE_CATEGORY:
            competitive.append(text)
    return internal, competitive


def synthesize_account_plan(company: str, internal: list, competitive: list):
    """Run the LLM synthesis from research text. Returns (status, plan, message).

    `internal` and `competitive` are lists of research text blocks. This is the
    core Agent 4 step, shared by the mem0-backed and direct (no-memory) paths.
    """
    if not OPENAI_API_KEY:
        return "FAIL", None, "Missing OpenAI API key (openAI_key) in .env.demo."
    if not internal and not competitive:
        return "FAIL", None, "No usable research to analyze."

    context = (
        f"PRIMARY COMPANY: {company}\n\n"
        "=== INTERNAL RESEARCH (about the primary company) ===\n"
        + ("\n\n".join(internal) or "(none)")
        + "\n\n=== COMPETITOR RESEARCH ===\n"
        + ("\n\n".join(competitive) or "(none)")
    )

    logger.info(
        "Agent 4 (account plan): synthesizing '%s' (%d internal, %d competitor blocks).",
        company,
        len(internal),
        len(competitive),
    )

    try:
        llm = ChatOpenAI(model=MODEL, api_key=OPENAI_API_KEY)
        analyst = llm.with_structured_output(AccountPlan)
        plan = analyst.invoke(
            [
                ("system", SYSTEM_PROMPT),
                ("human", f"Build the account plan for {company}.\n\n{context}"),
            ]
        )
    except Exception as exc:
        logger.warning("Agent 4: synthesis failed: %s", exc)
        return "FAIL", None, f"LLM synthesis failed: {exc}"

    for field, values in plan.model_dump().items():
        logger.info("Agent 4: %s (%d)", field, len(values))
        for value in values:
            logger.info("   - %s", value)

    return "PASS", plan, f"Built account plan for '{company}'."


def build_account_plan(company: str):
    """mem0-backed Agent 4: read the latest run from mem0, then synthesize."""
    run_ts, memories = read_latest_run()
    if not memories:
        return "FAIL", None, "No memories in mem0 to analyze."
    internal, competitive = _partition(memories)
    return synthesize_account_plan(company, internal, competitive)

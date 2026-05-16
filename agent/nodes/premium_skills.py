"""
NEXUS-thirdy | agent/nodes/premium_skills.py
Phase 6 — Premium Skills (Full Implementation)

Replaces the Phase 3 stub.
Uses NVIDIA NIM 70B for deepest reasoning.
Falls back to Cerebras 70B if NVIDIA credits are exhausted.
Output goes to Reflexion node for scoring and retry.
"""

import httpx
import json
from langchain_core.messages import SystemMessage, HumanMessage
from config.settings import settings
from config.skill_registry import SKILL_REGISTRY
import structlog

log = structlog.get_logger()


# ── LLM CLIENTS ───────────────────────────────────────────────────────────────

def _get_nvidia_llm():
    """NVIDIA NIM 70B — highest reasoning quality."""
    try:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=settings.NVIDIA_API_KEY,
            model="nvidia/llama-3.1-nemotron-70b-instruct",
            temperature=0.3,
            max_tokens=800
        )
    except Exception as e:
        log.error("nvidia_llm_init_failed", error=str(e))
        return None


def _get_cerebras_llm():
    """Cerebras 70B — fast + smart fallback."""
    try:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            base_url="https://api.cerebras.ai/v1",
            api_key=settings.CEREBRAS_API_KEY,
            model="llama3.1-70b",
            temperature=0.3,
            max_tokens=800
        )
    except Exception as e:
        log.error("cerebras_llm_init_failed", error=str(e))
        return None


# ── SKILL PROMPTS ─────────────────────────────────────────────────────────────

PREMIUM_PROMPTS = {
    "crypto_intelligence": """You are NEXUS-thirdy's premium crypto analyst.
Provide a deep analysis of the requested crypto asset. Include:
1. Current market context and price action narrative
2. On-chain signal interpretation (if relevant)
3. Sentiment assessment (bullish/bearish/neutral with reasoning)
4. Risk rating (1-10, with specific risk factors)
5. One specific actionable insight (entry zone, exit level, or wait signal)

Be specific with numbers where possible. Avoid generic statements.
User context: {context}""",

    "defi_yield_finder": """You are NEXUS-thirdy's DeFi yield specialist.
Analyze DeFi yield opportunities for the user's query. Include:
1. Top 3 protocols relevant to their request (with current estimated APY ranges)
2. Risk assessment for each (smart contract risk, IL risk, liquidity risk)
3. Match to implied risk tolerance
4. One specific recommended action with reasoning

Be direct. No fluff. User context: {context}""",

    "market_brief": """You are NEXUS-thirdy's market analyst.
Provide a concise market brief covering:
1. Top 3 notable market movements today and why they matter
2. Macro context in 2 sentences (DXY, rates, risk sentiment)
3. One actionable insight for the next 24-48 hours

Maximum 250 words. Dense, information-rich. No filler.""",

    "sentiment_scan": """You are NEXUS-thirdy's sentiment analyst.
Analyze market sentiment for the requested asset. Cover:
1. Social sentiment signal (X/Reddit tone: bullish/bearish/neutral)
2. On-chain behavior signal (accumulation vs distribution indicators)
3. Derivatives market signal (funding rates, OI trend if relevant)
4. Combined sentiment score (1-10, where 1=extreme fear, 10=extreme greed)
5. What this means for the next 48 hours

Be specific. Cite the signals you're reading.""",

    "portfolio_tracker": """You are NEXUS-thirdy's portfolio advisor.
Based on the user's portfolio information:
1. Summarize current allocation and concentration risk
2. Calculate implied P&L direction based on current market conditions
3. Identify the single biggest risk exposure
4. Suggest one specific rebalancing action if needed

User context and portfolio data: {context}""",
}

DEFAULT_PREMIUM_PROMPT = """You are NEXUS-thirdy's premium analysis engine.
Provide a thorough, expert-level response to the user's query.
Be specific, data-driven, and actionable. No generic statements.
User context: {context}"""


async def _invoke_with_fallback(messages: list) -> tuple[str, str]:
    """
    Try NVIDIA NIM first. Fall back to Cerebras if NVIDIA fails.
    Returns (response_text, model_used).
    """
    # Try NVIDIA first
    if settings.NVIDIA_API_KEY:
        nvidia = _get_nvidia_llm()
        if nvidia:
            try:
                result = await nvidia.ainvoke(messages)
                return result.content.strip(), "nvidia"
            except Exception as e:
                log.warning("nvidia_failed_falling_back", error=str(e)[:100])

    # Fall back to Cerebras
    if settings.CEREBRAS_API_KEY:
        cerebras = _get_cerebras_llm()
        if cerebras:
            try:
                result = await cerebras.ainvoke(messages)
                return result.content.strip(), "cerebras"
            except Exception as e:
                log.error("cerebras_failed", error=str(e)[:100])

    return "", "none"


# ── PREMIUM SKILLS NODE ───────────────────────────────────────────────────────

async def premium_skills_node(state: dict) -> dict:
    """
    Executes premium skills using NVIDIA NIM 70B (or Cerebras fallback).
    Sets llm_response and reasoning_trace in state.
    Output is scored by the Reflexion node next.
    """
    skill_id = state.get("detected_skill", "")
    skill = SKILL_REGISTRY.get(skill_id)

    if not skill:
        state["llm_response"] = "Skill not found."
        state["final_response"] = "Skill not found."
        return state

    # Check if this is a retry with critique
    critique = state.get("reflexion_critique", "")
    iteration = state.get("reflexion_iteration", 0)

    raw_message = state.get("raw_message", "")
    context_pack = state.get("context_pack", "")

    # Get skill prompt
    prompt_template = PREMIUM_PROMPTS.get(skill_id, DEFAULT_PREMIUM_PROMPT)
    system_prompt = prompt_template.format(context=context_pack or "No prior context.")

    # On retry: inject the critique
    if iteration > 0 and critique:
        system_prompt += f"\n\nIMPORTANT — Previous attempt was scored low. Fix this: {critique}"
        log.info("premium_retry", skill=skill_id, iteration=iteration, critique=critique[:100])

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=raw_message)
    ]

    response_text, model_used = await _invoke_with_fallback(messages)

    if not response_text:
        state["llm_response"] = (
            f"**{skill.name}** is temporarily unavailable. "
            "Premium LLM quota exhausted. Please try again in a few hours."
        )
        state["final_response"] = state["llm_response"]
        log.error("premium_all_llms_failed", skill=skill_id)
        return state

    state["llm_response"] = response_text
    state["reasoning_trace"] = f"Model: {model_used} | Skill: {skill_id} | Iteration: {iteration + 1}"

    log.info(
        "premium_skill_executed",
        skill=skill_id,
        model=model_used,
        user_id=state.get("user_id"),
        iteration=iteration + 1
    )

    return state

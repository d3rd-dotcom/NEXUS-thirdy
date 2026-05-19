"""
NEXUS-thirdy | agent/nodes/premium_skills.py
Phase 6 — Premium Skills Node

Uses NVIDIA NIM 70B for deepest reasoning; falls back to Cerebras 70B.
Output is scored and optionally retried by the Reflexion node.

FIXED (M7): LLM client instances are now module-level singletons. Previously
            `_get_nvidia_llm()` and `_get_cerebras_llm()` created a brand-new
            ChatOpenAI (and a new underlying HTTP session) on every single
            premium skill invocation. Under load this exhausted file descriptors
            and inflated TLS handshake latency noticeably.

FIXED (H2): Lazy initialisation prevents crashes when API keys are absent.
            Clients are only constructed on first call, not at import time.
"""

from langchain_core.messages import SystemMessage, HumanMessage
from config.settings import settings
from config.skill_registry import SKILL_REGISTRY
import structlog

log = structlog.get_logger()


# ── CACHED LLM CLIENTS ────────────────────────────────────────────────────────
# FIXED (M7): Module-level singletons — created once, reused across all calls.
# Previously both getters unconditionally called ChatOpenAI(...) every time,
# meaning each premium request opened a fresh connection pool.

_nvidia_llm = None
_cerebras_llm = None


def _get_nvidia_llm():
    """Return cached NVIDIA NIM client, creating it on first call."""
    global _nvidia_llm
    # FIXED (M7): Guard prevents re-instantiation on subsequent calls
    if _nvidia_llm is None and settings.NVIDIA_API_KEY:
        try:
            from langchain_openai import ChatOpenAI
            _nvidia_llm = ChatOpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=settings.NVIDIA_API_KEY,
                model="nvidia/llama-3.1-nemotron-70b-instruct",
                temperature=0.3,
                max_tokens=800,
            )
            log.info("nvidia_llm_initialized")
        except Exception as e:
            log.error("nvidia_llm_init_failed", error=str(e))
    return _nvidia_llm


def _get_cerebras_llm():
    """Return cached Cerebras client, creating it on first call."""
    global _cerebras_llm
    # FIXED (M7): Guard prevents re-instantiation on subsequent calls
    if _cerebras_llm is None and settings.CEREBRAS_API_KEY:
        try:
            from langchain_openai import ChatOpenAI
            _cerebras_llm = ChatOpenAI(
                base_url="https://api.cerebras.ai/v1",
                api_key=settings.CEREBRAS_API_KEY,
                model="llama3.1-70b",
                temperature=0.3,
                max_tokens=800,
            )
            log.info("cerebras_llm_initialized")
        except Exception as e:
            log.error("cerebras_llm_init_failed", error=str(e))
    return _cerebras_llm


# ── SKILL PROMPTS ─────────────────────────────────────────────────────────────

PREMIUM_PROMPTS = {
    "crypto_intelligence": (
        "You are NEXUS-thirdy's premium crypto analyst.\n"
        "Provide a deep analysis of the requested crypto asset. Include:\n"
        "1. Current market context and price action narrative\n"
        "2. On-chain signal interpretation (if relevant)\n"
        "3. Sentiment assessment (bullish/bearish/neutral with reasoning)\n"
        "4. Risk rating (1-10, with specific risk factors)\n"
        "5. One specific actionable insight (entry zone, exit level, or wait signal)\n"
        "Be specific with numbers where possible. Avoid generic statements.\n"
        "User context: {context}"
    ),
    "defi_yield_finder": (
        "You are NEXUS-thirdy's DeFi yield specialist.\n"
        "Analyse DeFi yield opportunities for the user's query. Include:\n"
        "1. Top 3 protocols relevant to their request (with current estimated APY ranges)\n"
        "2. Risk assessment for each (smart contract risk, IL risk, liquidity risk)\n"
        "3. Match to implied risk tolerance\n"
        "4. One specific recommended action with reasoning\n"
        "Be direct. No fluff. User context: {context}"
    ),
    "market_brief": (
        "You are NEXUS-thirdy's market analyst.\n"
        "Provide a concise market brief covering:\n"
        "1. Top 3 notable market movements today and why they matter\n"
        "2. Macro context in 2 sentences (DXY, rates, risk sentiment)\n"
        "3. One actionable insight for the next 24-48 hours\n"
        "Maximum 250 words. Dense, information-rich. No filler."
    ),
    "sentiment_scan": (
        "You are NEXUS-thirdy's sentiment analyst.\n"
        "Analyse market sentiment for the requested asset. Cover:\n"
        "1. Social sentiment signal (X/Reddit tone: bullish/bearish/neutral)\n"
        "2. On-chain behavior signal (accumulation vs distribution indicators)\n"
        "3. Derivatives market signal (funding rates, OI trend if relevant)\n"
        "4. Combined sentiment score (1-10, where 1=extreme fear, 10=extreme greed)\n"
        "5. What this means for the next 48 hours\n"
        "Be specific. Cite the signals you're reading."
    ),
    "portfolio_tracker": (
        "You are NEXUS-thirdy's portfolio advisor.\n"
        "Based on the user's portfolio information:\n"
        "1. Summarise current allocation and concentration risk\n"
        "2. Calculate implied P&L direction based on current market conditions\n"
        "3. Identify the single biggest risk exposure\n"
        "4. Suggest one specific rebalancing action if needed\n"
        "User context and portfolio data: {context}"
    ),
}

DEFAULT_PREMIUM_PROMPT = (
    "You are NEXUS-thirdy's premium analysis engine.\n"
    "Provide a thorough, expert-level response to the user's query.\n"
    "Be specific, data-driven, and actionable. No generic statements.\n"
    "User context: {context}"
)


# ── LLM INVOCATION WITH FALLBACK ─────────────────────────────────────────────

async def _invoke_with_fallback(messages: list) -> tuple[str, str]:
    """
    Try NVIDIA NIM first; fall back to Cerebras if NVIDIA fails.
    Returns (response_text, model_used_label).
    """
    # FIXED (M7): _get_nvidia_llm() now returns the cached singleton
    nvidia = _get_nvidia_llm()
    if nvidia:
        try:
            result = await nvidia.ainvoke(messages)
            return result.content.strip(), "nvidia"
        except Exception as e:
            log.warning("nvidia_failed_falling_back", error=str(e)[:100])

    # FIXED (M7): _get_cerebras_llm() now returns the cached singleton
    cerebras = _get_cerebras_llm()
    if cerebras:
        try:
            result = await cerebras.ainvoke(messages)
            return result.content.strip(), "cerebras"
        except Exception as e:
            log.error("cerebras_failed", error=str(e)[:100])

    return "", "none"


# ── NODE ──────────────────────────────────────────────────────────────────────

async def premium_skills_node(state: dict) -> dict:
    """
    Executes premium skills using NVIDIA NIM 70B (Cerebras 70B fallback).
    Writes llm_response and reasoning_trace into state.
    Output is scored and optionally retried by the Reflexion node.
    """
    skill_id = state.get("detected_skill", "")
    skill = SKILL_REGISTRY.get(skill_id)

    if not skill:
        state["llm_response"] = "Skill not found."
        state["final_response"] = "Skill not found."
        return state

    critique = state.get("reflexion_critique", "")
    iteration = state.get("reflexion_iteration", 0)
    raw_message = state.get("raw_message", "")
    context_pack = state.get("context_pack", "")

    prompt_template = PREMIUM_PROMPTS.get(skill_id, DEFAULT_PREMIUM_PROMPT)
    system_prompt = prompt_template.format(context=context_pack or "No prior context.")

    # On retry: inject the critique from the Reflexion node so the model
    # knows exactly what to improve on the second or third attempt
    if iteration > 0 and critique:
        system_prompt += (
            f"\n\nIMPORTANT — Previous attempt scored below threshold. "
            f"Fix this specific issue: {critique}"
        )
        log.info(
            "premium_retry",
            skill=skill_id,
            iteration=iteration,
            critique=critique[:120],
        )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=raw_message),
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
    state["reasoning_trace"] = (
        f"Model: {model_used} | Skill: {skill_id} | Iteration: {iteration + 1}"
    )

    log.info(
        "premium_skill_executed",
        skill=skill_id,
        model=model_used,
        user_id=state.get("user_id"),
        iteration=iteration + 1,
    )
    return state

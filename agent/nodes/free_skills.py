"""
NEXUS-thirdy | agent/nodes/free_skills.py
Phase 3 — Free Skills Node

Handles all free skills using Groq Llama 3.1 8B.
Fast, cheap, no payment required.

FIXED (H2): LLM client now uses lazy initialisation. Previously
            `_llm = ChatGroq(...)` was called at module-import time, causing
            AuthenticationError on any import without GROQ_API_KEY set
            (CI pipelines, cold starts, local tests without .env).

FIXED (perf): Free and premium skill counts are now computed once at module
              load and cached as `_FREE_COUNT` / `_PREMIUM_COUNT`. Previously
              `len(get_free_skills())` was called on every single greet request.

FIXED (M8): Removed the `_payment_required` detected_skill check. Payment
            routing is handled exclusively by the payment_gate node in
            agent/graph.py — this node should never see that string.
"""

from langchain_core.messages import SystemMessage, HumanMessage
from config.settings import settings
from config.skill_registry import get_free_skills, get_premium_skills
import structlog
import random

log = structlog.get_logger()


# ── CACHED SKILL COUNTS ───────────────────────────────────────────────────────
# FIXED (perf): These are compile-time constants — SKILL_REGISTRY never changes
# at runtime. Compute once here so the greet prompt incurs zero dict lookups
# per request.
_FREE_COUNT: int = len(get_free_skills())
_PREMIUM_COUNT: int = len(get_premium_skills())


# ── LAZY LLM CLIENT ───────────────────────────────────────────────────────────
# FIXED (H2): Replaced module-level `_llm = ChatGroq(...)` with a lazy getter.
# The client is created on first use and cached for all subsequent calls.

_llm = None


def _get_llm():
    global _llm
    if _llm is None and settings.GROQ_API_KEY:
        from langchain_groq import ChatGroq
        _llm = ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model="llama-3.1-8b-instant",
            temperature=0.7,
            max_tokens=400,
        )
    return _llm


# ── SKILL PROMPTS ─────────────────────────────────────────────────────────────

SKILL_PROMPTS = {
    "greet": (
        "You are NEXUS-thirdy, an intelligent AI agent specialising in crypto, "
        "Web3, and AI. Give a warm, confident greeting. Mention you have "
        "{free_count} free skills and {premium_count} premium skills. "
        "Be concise — max 3 sentences. Sound like a sharp, friendly expert, "
        "not a generic chatbot."
    ),
    "crypto_price": (
        "You are NEXUS-thirdy. The user wants to know the price of a cryptocurrency. "
        "Acknowledge you would fetch real-time data, give a helpful response about "
        "the asset they asked about, and note that live price data integration is "
        "coming soon. Be direct and informative."
    ),
    "weather": (
        "You are NEXUS-thirdy. The user wants weather information. "
        "Acknowledge their location request, note that live weather API integration "
        "is coming soon, and provide general climate information about that location. "
        "Be helpful."
    ),
    "wisdom": (
        "You are NEXUS-thirdy. Share one powerful piece of wisdom relevant to "
        "investing, technology, or life philosophy. Keep it to 2-3 sentences. "
        "Make it genuinely insightful, not generic. Draw from stoicism, tech "
        "culture, or financial wisdom."
    ),
    "joke": (
        "You are NEXUS-thirdy. Tell one clever, original joke about crypto, "
        "blockchain, AI, or Web3. Keep it clean and actually funny. "
        "Aim for something a tech-savvy person would appreciate."
    ),
    "define": (
        "You are NEXUS-thirdy, a crypto and Web3 expert. The user wants a "
        "definition. Give a clear, accurate explanation in 2-3 sentences. "
        "Use plain language first, then one technical detail. "
        "End with one real-world example."
    ),
    "agent_status": (
        "You are NEXUS-thirdy. Report your current status confidently. "
        "Mention: always-on server-native deployment on Render, your skill count, "
        "current development phase, and your permanent URL."
    ),
    "flip": (
        "You are NEXUS-thirdy. The user wants a coin flip. "
        "Make the result dramatic and fun. Announce heads or tails clearly."
    ),
    "translate": (
        "You are NEXUS-thirdy. Translate the user's text accurately. "
        "Identify source and target language from context. "
        "If no target language is specified, translate to English. "
        "Provide only the translation, no extra commentary."
    ),
    "summarize": (
        "You are NEXUS-thirdy. Summarise the provided text into 3-5 bullet points. "
        "Each bullet should be one clear sentence. "
        "Focus on the most important information. Be concise."
    ),
}

# Fallback for any skill id not in the map above
DEFAULT_PROMPT = (
    "You are NEXUS-thirdy, a helpful AI agent specialising in crypto, Web3, and AI. "
    "Answer the user's question helpfully and concisely."
)


# ── NODE ──────────────────────────────────────────────────────────────────────

async def free_skills_node(state: dict) -> dict:
    """
    Executes the detected free skill using Groq 8B.
    Writes final_response into state.
    """
    skill_id = state.get("detected_skill", "greet")
    raw_message = state.get("raw_message", "")
    context_pack = state.get("context_pack", "")

    system_prompt = SKILL_PROMPTS.get(skill_id, DEFAULT_PROMPT)

    # Inject cached counts for greet — no per-request dict lookup needed
    if "{free_count}" in system_prompt:
        # FIXED (perf): Use module-level constants, not live len() calls
        system_prompt = system_prompt.format(
            free_count=_FREE_COUNT,
            premium_count=_PREMIUM_COUNT,
        )

    # Coin flip requires no LLM — handle inline
    if skill_id == "flip":
        result = random.choice(["HEADS", "TAILS"])
        state["final_response"] = (
            f"🪙 The coin spins... and lands on **{result}**! "
            + (
                "Fortune favors you! 🎉"
                if result == "HEADS"
                else "The market flips like a coin. 🎲"
            )
        )
        log.info("free_skill_executed", skill=skill_id, method="hardcoded")
        return state

    # Build message list — inject context pack when available
    messages = [SystemMessage(content=system_prompt)]
    if context_pack and context_pack != "No memory context yet.":
        messages.append(
            SystemMessage(content=f"Context about this user:\n{context_pack}")
        )
    messages.append(HumanMessage(content=raw_message))

    llm = _get_llm()
    if llm is None:
        # Degrade gracefully rather than crashing — key not configured
        state["final_response"] = (
            "Language model not available. Please check GROQ_API_KEY configuration."
        )
        log.error("free_skill_no_llm", skill=skill_id)
        return state

    try:
        response = await llm.ainvoke(messages)
        state["final_response"] = response.content.strip()
        log.info("free_skill_executed", skill=skill_id, user_id=state.get("user_id"))
    except Exception as e:
        log.error("free_skill_error", skill=skill_id, error=str(e))
        state["final_response"] = (
            "I encountered an issue processing that request. Please try again."
        )

    return state

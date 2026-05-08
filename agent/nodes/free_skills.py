"""
NEXUS-thirdy | agent/nodes/free_skills.py
Phase 3 — Free Skills Node

Handles all free skills using Groq 8B.
Fast, cheap, no payment required.
Each skill has its own system prompt for best results.
"""

from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from config.settings import settings
from config.skill_registry import SKILL_REGISTRY
import structlog
import random

log = structlog.get_logger()

_llm = ChatGroq(
    api_key=settings.GROQ_API_KEY,
    model="llama-3.1-8b-instant",
    temperature=0.7,
    max_tokens=400
)

# ── SKILL PROMPTS ─────────────────────────────────────────────────────────────
# Each skill gets a focused system prompt.
# Keeps responses tight and on-brand.

SKILL_PROMPTS = {
    "greet": """You are NEXUS-thirdy, an intelligent AI agent specializing in crypto, 
Web3, and AI. Give a warm, confident greeting. Mention you have {free_count} free skills 
and {premium_count} premium skills. Be concise — max 3 sentences. Sound like a sharp, 
friendly expert, not a generic chatbot.""",

    "crypto_price": """You are NEXUS-thirdy. The user wants to know the price of a 
cryptocurrency. Acknowledge you would fetch real-time data, give a helpful response 
about the asset they asked about, and note that live price data integration is coming soon. 
Be direct and informative.""",

    "weather": """You are NEXUS-thirdy. The user wants weather information. 
Acknowledge their location request, note that live weather API integration is coming soon, 
and provide any general information you can about the climate of that location. Be helpful.""",

    "wisdom": """You are NEXUS-thirdy. Share one powerful piece of wisdom relevant to 
investing, technology, or life philosophy. Keep it to 2-3 sentences. Make it feel 
genuinely insightful, not generic. Draw from stoicism, tech culture, or financial wisdom.""",

    "joke": """You are NEXUS-thirdy. Tell one clever, original joke about crypto, 
blockchain, AI, or Web3. Keep it clean and actually funny. No groan-worthy dad jokes — 
aim for something a tech-savvy person would genuinely appreciate.""",

    "define": """You are NEXUS-thirdy, a crypto and Web3 expert. The user wants a 
definition. Give a clear, accurate explanation in 2-3 sentences. Use plain language first, 
then add one technical detail. End with one real-world example.""",

    "agent_status": """You are NEXUS-thirdy. Report your current status. Be confident 
and informative. Mention: you are server-native (always-on), your skill count, 
your current phase of development, and your permanent URL.""",

    "flip": """You are NEXUS-thirdy. The user wants a coin flip. 
Make the result dramatic and fun. Announce heads or tails clearly.""",

    "translate": """You are NEXUS-thirdy. Translate the user's text accurately. 
Identify the source language and target language from context. 
If no target language is specified, translate to English. 
Provide only the translation, no extra commentary.""",

    "summarize": """You are NEXUS-thirdy. Summarize the provided text into 3-5 
bullet points. Each bullet should be one clear sentence. 
Focus on the most important information. Be concise.""",
}

# Default prompt for any skill not in the map
DEFAULT_PROMPT = """You are NEXUS-thirdy, a helpful AI agent specializing in 
crypto, Web3, and AI. Answer the user's question helpfully and concisely."""


async def free_skills_node(state: dict) -> dict:
    """
    Executes the detected free skill using Groq 8B.
    Sets final_response in state.
    """
    skill_id = state.get("detected_skill", "greet")
    raw_message = state.get("raw_message", "")
    context_pack = state.get("context_pack", "")

    from config.skill_registry import get_free_skills, get_premium_skills
    free_count = len(get_free_skills())
    premium_count = len(get_premium_skills())

    # Get skill prompt
    system_prompt = SKILL_PROMPTS.get(skill_id, DEFAULT_PROMPT)

    # Inject skill counts for greet
    system_prompt = system_prompt.format(
        free_count=free_count,
        premium_count=premium_count
    ) if "{free_count}" in system_prompt else system_prompt

    # Special case: coin flip doesn't need LLM
    if skill_id == "flip":
        result = random.choice(["HEADS", "TAILS"])
        state["final_response"] = (
            f"🪙 The coin spins... and lands on **{result}**! "
            f"{'Fortune favors you! 🎉' if result == 'HEADS' else 'The market flips like a coin. 🎲'}"
        )
        log.info("free_skill_executed", skill=skill_id, method="hardcoded")
        return state

    # Build messages — include context pack if available
    messages = [SystemMessage(content=system_prompt)]
    if context_pack and context_pack != "No memory context yet.":
        messages.append(SystemMessage(content=f"Context about this user:\n{context_pack}"))
    messages.append(HumanMessage(content=raw_message))

    try:
        response = await _llm.ainvoke(messages)
        state["final_response"] = response.content.strip()
        log.info("free_skill_executed", skill=skill_id, user_id=state.get("user_id"))

    except Exception as e:
        log.error("free_skill_error", skill=skill_id, error=str(e))
        state["final_response"] = (
            "I encountered an issue processing that request. Please try again."
        )

    return state

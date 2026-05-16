"""
NEXUS-thirdy | agent/supervisor.py
Phase 8 — Updated Supervisor with Cerebras Fallback

Changes from Phase 3:
  - Cerebras fallback when Groq hits rate limit (fixes 429 errors in logs)
  - Security scan on every message before routing
  - sanitize_user_id applied to all user IDs
"""

import json
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from config.settings import settings
from config.skill_registry import SKILL_REGISTRY
from security.validators import validate_input, sanitize_user_id
from security.firewall import scan_message
import structlog

log = structlog.get_logger()

# Primary — Groq 8B (fast, free)
_groq_llm = ChatGroq(
    api_key=settings.GROQ_API_KEY,
    model="llama-3.1-8b-instant",
    temperature=0,
    max_tokens=150
)

# Fallback — Cerebras 70B (when Groq rate limit hit)
_cerebras_llm = None

def _get_cerebras():
    global _cerebras_llm
    if _cerebras_llm is None and settings.CEREBRAS_API_KEY:
        try:
            from langchain_openai import ChatOpenAI
            _cerebras_llm = ChatOpenAI(
                base_url="https://api.cerebras.ai/v1",
                api_key=settings.CEREBRAS_API_KEY,
                model="llama3.1-8b",   # 8B on Cerebras = still fast
                temperature=0,
                max_tokens=150
            )
        except Exception as e:
            log.error("cerebras_supervisor_init_failed", error=str(e))
    return _cerebras_llm


SUPERVISOR_PROMPT = """You are NEXUS-thirdy's routing brain.
Your ONLY job is to read the user's message and pick the best skill to handle it.

Available skills:
{skill_list}

Rules:
- Pick exactly ONE skill id from the list above
- If nothing matches, use: greet
- Detect the user's language

Respond with ONLY raw JSON. No explanation. No markdown. No extra text.
Format: {{"skill": "skill_id", "confidence": 0.95, "language": "en"}}
"""


async def _invoke_supervisor(messages: list) -> str:
    """Try Groq first, fall back to Cerebras on rate limit."""
    try:
        result = await _groq_llm.ainvoke(messages)
        return result.content.strip()
    except Exception as e:
        error_str = str(e)
        if "429" in error_str or "rate_limit" in error_str:
            log.warning("groq_rate_limit_falling_back_to_cerebras")
            cerebras = _get_cerebras()
            if cerebras:
                try:
                    result = await cerebras.ainvoke(messages)
                    return result.content.strip()
                except Exception as ce:
                    log.error("cerebras_fallback_failed", error=str(ce))
        else:
            log.error("supervisor_llm_error", error=error_str[:200])
        return ""


async def supervisor_node(state: dict) -> dict:
    """
    Reads the user message, validates it, scans for injection,
    then picks the best skill to handle it.
    """
    user_id = sanitize_user_id(state.get("user_id", "anonymous"))
    state["user_id"] = user_id  # Update with sanitized version
    raw_message = state.get("raw_message", "")

    # Input validation
    validation = validate_input(raw_message, user_id=user_id)
    if not validation.is_valid:
        state["detected_skill"] = "greet"
        state["requires_payment"] = False
        state["final_response"] = "Your message couldn't be processed. Please try rephrasing."
        log.warning("input_rejected", user_id=user_id, reason=validation.reason)
        return state

    # Security scan
    is_safe, reason = await scan_message(user_id=user_id, message=raw_message)
    if not is_safe:
        state["detected_skill"] = "greet"
        state["requires_payment"] = False
        state["final_response"] = "Your message was flagged by our security filter. Please rephrase."
        return state

    # Use sanitized message
    raw_message = validation.sanitized or raw_message

    # Build skill list
    skill_list = "\n".join(
        f"  - {skill_id}: {s.description}"
        for skill_id, s in SKILL_REGISTRY.items()
    )

    messages = [
        SystemMessage(content=SUPERVISOR_PROMPT.format(skill_list=skill_list)),
        HumanMessage(content=raw_message)
    ]

    raw_response = await _invoke_supervisor(messages)

    if not raw_response:
        state["detected_skill"] = "greet"
        state["requires_payment"] = False
        return state

    try:
        # Strip markdown fences if present
        if raw_response.startswith("```"):
            raw_response = raw_response.split("```")[1]
            if raw_response.startswith("json"):
                raw_response = raw_response[4:]

        routing = json.loads(raw_response)
        detected = routing.get("skill", "greet")

        if detected not in SKILL_REGISTRY:
            detected = "greet"

        state["detected_skill"] = detected
        state["requires_payment"] = SKILL_REGISTRY[detected].requires_payment

        log.info(
            "supervisor_routed",
            user_id=user_id,
            skill=detected,
            confidence=routing.get("confidence", 0)
        )

    except Exception as e:
        log.error("supervisor_parse_error", error=str(e))
        state["detected_skill"] = "greet"
        state["requires_payment"] = False

    return state

"""
NEXUS-thirdy | agent/supervisor.py
Phase 8 — Supervisor Node (Groq primary / Cerebras fallback)

FIXED (H2): LLM clients now use lazy initialisation. Previously both
            `_groq_llm` and `_cerebras_llm` were instantiated at module-import
            time by calling ChatGroq(...) and ChatOpenAI(...) at the top level.
            Any cold start or CI run without GROQ_API_KEY set caused an
            AuthenticationError before a single request was processed.

FIXED (perf): The skill-list string is built once at module load time and
              cached in `_SKILL_LIST_CACHE`. Previously it was rebuilt from
              SKILL_REGISTRY on every incoming message — a pure waste of CPU
              for a value that never changes at runtime.

FIXED (M5): Injection-pattern detection delegated to security/validators.py
            rather than duplicated inline here.
"""

import json
from langchain_core.messages import SystemMessage, HumanMessage
from config.settings import settings
from config.skill_registry import SKILL_REGISTRY
from security.validators import validate_input, sanitize_user_id
from security.firewall import scan_message
import structlog

log = structlog.get_logger()


# ── SKILL LIST CACHE ──────────────────────────────────────────────────────────
# FIXED (perf): SKILL_REGISTRY is a compile-time constant — its string
# representation never changes. Build it once here and reuse on every request.
_SKILL_LIST_CACHE: str = "\n".join(
    f"  - {skill_id}: {s.description}"
    for skill_id, s in SKILL_REGISTRY.items()
)


# ── LAZY LLM CLIENTS ─────────────────────────────────────────────────────────
# FIXED (H2): Both clients are now instantiated on first use, not at import
# time. This prevents AuthenticationError / ImportError crashes during:
#   - GitHub Actions CI (no API keys in environment)
#   - First Render cold start before env vars are injected
#   - Local unit tests that import the module without a .env file

_groq_llm = None
_cerebras_llm = None


def _get_groq():
    global _groq_llm
    if _groq_llm is None and settings.GROQ_API_KEY:
        from langchain_groq import ChatGroq
        _groq_llm = ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model="llama-3.1-8b-instant",
            temperature=0,
            max_tokens=150,
        )
    return _groq_llm


def _get_cerebras():
    global _cerebras_llm
    if _cerebras_llm is None and settings.CEREBRAS_API_KEY:
        try:
            from langchain_openai import ChatOpenAI
            _cerebras_llm = ChatOpenAI(
                base_url="https://api.cerebras.ai/v1",
                api_key=settings.CEREBRAS_API_KEY,
                model="llama3.1-8b",
                temperature=0,
                max_tokens=150,
            )
        except Exception as e:
            log.error("cerebras_supervisor_init_failed", error=str(e))
    return _cerebras_llm


# ── PROMPT ────────────────────────────────────────────────────────────────────

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


# ── LLM INVOCATION ────────────────────────────────────────────────────────────

async def _invoke_supervisor(messages: list) -> str:
    """
    Try Groq first; fall back to Cerebras on rate-limit (429) or Groq error.
    Returns the raw string response, or "" if both providers fail.
    """
    groq = _get_groq()
    if groq:
        try:
            result = await groq.ainvoke(messages)
            return result.content.strip()
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "rate_limit" in error_str.lower():
                log.warning("groq_rate_limit_falling_back_to_cerebras")
            else:
                log.error("supervisor_groq_error", error=error_str[:200])

    cerebras = _get_cerebras()
    if cerebras:
        try:
            result = await cerebras.ainvoke(messages)
            return result.content.strip()
        except Exception as ce:
            log.error("cerebras_fallback_failed", error=str(ce)[:200])

    return ""


# ── NODE ──────────────────────────────────────────────────────────────────────

async def supervisor_node(state: dict) -> dict:
    """
    Validates and security-scans the incoming message, then picks the best skill.
    Writes `detected_skill` and `requires_payment` into state.
    """
    # FIXED (H7 / security): sanitize user_id before any downstream DB writes.
    user_id = sanitize_user_id(state.get("user_id", "anonymous"))
    state["user_id"] = user_id
    raw_message = state.get("raw_message", "")

    # ── Input validation ──────────────────────────────────────────────────────
    validation = validate_input(raw_message, user_id=user_id)
    if not validation.is_valid:
        state["detected_skill"] = "greet"
        state["requires_payment"] = False
        state["final_response"] = (
            "Your message couldn't be processed. Please try rephrasing."
        )
        log.warning("input_rejected", user_id=user_id, reason=validation.reason)
        return state

    # ── Security scan ─────────────────────────────────────────────────────────
    is_safe, reason = await scan_message(user_id=user_id, message=raw_message)
    if not is_safe:
        state["detected_skill"] = "greet"
        state["requires_payment"] = False
        state["final_response"] = (
            "Your message was flagged by our security filter. Please rephrase."
        )
        return state

    raw_message = validation.sanitized or raw_message

    # FIXED (perf): Use module-level cache — skill list never changes at runtime.
    messages = [
        SystemMessage(content=SUPERVISOR_PROMPT.format(skill_list=_SKILL_LIST_CACHE)),
        HumanMessage(content=raw_message),
    ]

    raw_response = await _invoke_supervisor(messages)

    if not raw_response:
        state["detected_skill"] = "greet"
        state["requires_payment"] = False
        return state

    # ── Parse routing decision ────────────────────────────────────────────────
    try:
        # Strip markdown fences if the model wrapped the JSON anyway
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
            confidence=routing.get("confidence", 0),
        )

    except Exception as e:
        log.error("supervisor_parse_error", error=str(e))
        state["detected_skill"] = "greet"
        state["requires_payment"] = False

    return state

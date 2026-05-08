"""
NEXUS-thirdy | agent/supervisor.py
Phase 3 — Supervisor Node

The router. Runs on every single message.
Uses Groq 8B (fastest, free) to classify intent and identify which skill to run.
Keeps it cheap and fast — this node runs before anything else.
"""

import json
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from config.settings import settings
from config.skill_registry import SKILL_REGISTRY
import structlog

log = structlog.get_logger()

# Groq 8B — fast enough for routing, cheap enough to run on every message
_supervisor_llm = ChatGroq(
    api_key=settings.GROQ_API_KEY,
    model="llama-3.1-8b-instant",
    temperature=0,
    max_tokens=150
)

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


async def supervisor_node(state: dict) -> dict:
    """
    Reads the user message, picks a skill, sets detected_skill in state.
    Also builds the context pack (memory + graph) before routing.
    """
    user_id = state["user_id"]
    raw_message = state["raw_message"]

    # Build context pack from memory (Phase 4 adds real memory here)
    # For Phase 3: context is empty — memory layer not installed yet
    context_pack = state.get("context_pack", "No memory context yet.")
    state["context_pack"] = context_pack

    # Build skill list for the prompt
    skill_list = "\n".join(
        f"  - {skill_id}: {s.description}"
        for skill_id, s in SKILL_REGISTRY.items()
    )

    try:
        response = await _supervisor_llm.ainvoke([
            SystemMessage(content=SUPERVISOR_PROMPT.format(skill_list=skill_list)),
            HumanMessage(content=raw_message)
        ])

        raw = response.content.strip()

        # Strip markdown fences if model adds them anyway
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        routing = json.loads(raw)
        detected = routing.get("skill", "greet")

        # Validate — if model hallucinates a skill id, fall back to greet
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
        log.error("supervisor_error", error=str(e), user_id=user_id)
        state["detected_skill"] = "greet"
        state["requires_payment"] = False

    return state

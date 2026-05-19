"""
NEXUS-thirdy | agent/reflexion.py
Phase 6 — Reflexion Critic Node

Scores every premium skill output (1-10).
If score < 7 and iterations < 3: injects critique and retries.
If score >= 8: archives the trace to Graphiti as a reusable procedure.
Always terminates — max 3 iterations, never an infinite loop.

FIXED (H2): Critic LLM now uses lazy initialisation. Previously
            `_critic_llm = ChatGroq(...)` was called at module-import time,
            causing AuthenticationError on any import if GROQ_API_KEY was
            absent (CI, cold start, local tests without .env).
"""

import json
import asyncio
from langchain_core.messages import SystemMessage, HumanMessage
from config.settings import settings
import structlog

log = structlog.get_logger()


# ── LAZY CRITIC LLM ───────────────────────────────────────────────────────────
# FIXED (H2): Instantiated on first call to _get_critic_llm(), not at import.

_critic_llm = None


def _get_critic_llm():
    global _critic_llm
    if _critic_llm is None and settings.GROQ_API_KEY:
        from langchain_groq import ChatGroq
        _critic_llm = ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model="llama-3.1-8b-instant",
            temperature=0,
            max_tokens=300,
        )
    return _critic_llm


# ── PROMPT ────────────────────────────────────────────────────────────────────

CRITIC_PROMPT = """You are a strict quality critic for an AI agent.
Evaluate this response on three criteria:

1. ACCURACY (1-10): Are the facts correct? No unsupported claims?
2. COMPLETENESS (1-10): Does it fully answer the question?
3. ACTIONABILITY (1-10): Can the user act on this? Is it specific enough?

Skill type: {skill_name}
Original query: {query}
Response to evaluate:
{response}

Respond with ONLY raw JSON. No markdown. No explanation.
{{"accuracy": N, "completeness": N, "actionability": N, "average": N, "critique": "one specific improvement needed"}}
"""


# ── NODE ──────────────────────────────────────────────────────────────────────

async def reflexion_node(state: dict) -> dict:
    """
    Scores the premium skill output.
    Sets reflexion_score and reflexion_critique in state.
    Archives high-quality traces (score >= 8) to Graphiti as reusable procedures.
    """
    iteration = state.get("reflexion_iteration", 0) + 1
    state["reflexion_iteration"] = iteration

    skill_name = state.get("detected_skill", "unknown")
    query = state.get("raw_message", "")
    llm_response = state.get("llm_response", "")

    # Nothing to evaluate — pass through
    if not llm_response:
        state["reflexion_score"] = 0.0
        state["reflexion_critique"] = "No response generated"
        return state

    critic = _get_critic_llm()
    if critic is None:
        # No critic available (GROQ_API_KEY not set) — accept output as-is.
        log.warning("reflexion_no_critic_llm_accepting_output")
        state["reflexion_score"] = 10.0
        state["final_response"] = llm_response
        return state

    try:
        result = await critic.ainvoke([
            SystemMessage(content=CRITIC_PROMPT.format(
                skill_name=skill_name,
                query=query,
                response=llm_response,
            )),
            HumanMessage(content="Evaluate."),
        ])

        raw = result.content.strip()

        # Strip markdown fences if the model added them despite instructions
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        scores = json.loads(raw)
        avg_score = float(scores.get("average", 7.0))
        critique = scores.get("critique", "")

        state["reflexion_score"] = avg_score
        state["reflexion_critique"] = critique

        log.info(
            "reflexion_scored",
            skill=skill_name,
            score=avg_score,
            iteration=iteration,
        )

        # Accept output if score is sufficient or max iterations reached
        if avg_score >= 7.0 or iteration >= 3:
            state["final_response"] = llm_response

            # Archive high-quality traces to Graphiti for future reuse
            if avg_score >= 8.0:
                from memory.graphiti_store import nexus_graph_store
                asyncio.create_task(
                    nexus_graph_store.archive_procedure(
                        skill_name=skill_name,
                        inputs={"query": query},
                        reasoning_trace=state.get("reasoning_trace", ""),
                        final_output=llm_response,
                        reflexion_score=avg_score,
                    )
                )

    except Exception as e:
        log.error("reflexion_error", error=str(e))
        # On any error: accept the output to avoid blocking the user.
        state["reflexion_score"] = 10.0
        state["final_response"] = llm_response

    return state

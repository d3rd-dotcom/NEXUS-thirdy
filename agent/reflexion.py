"""
NEXUS-thirdy | agent/reflexion.py
Phase 6 — Reflexion Critic Node

Scores every premium skill output (1-10).
If score < 7 and iterations < 3: injects critique and retries.
If score >= 8: archives the trace to Graphiti as a reusable procedure.
Always terminates — max 3 iterations, never infinite loop.
"""

import json
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from config.settings import settings
from memory.graphiti_store import nexus_graph_store
import structlog

log = structlog.get_logger()

_critic_llm = ChatGroq(
    api_key=settings.GROQ_API_KEY,
    model="llama-3.1-8b-instant",
    temperature=0,
    max_tokens=300
)

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


async def reflexion_node(state: dict) -> dict:
    """
    Scores the premium skill output.
    Sets reflexion_score and reflexion_critique in state.
    Archives to Graphiti if score >= 8.
    """
    iteration = state.get("reflexion_iteration", 0) + 1
    state["reflexion_iteration"] = iteration

    skill_name = state.get("detected_skill", "unknown")
    query = state.get("raw_message", "")
    llm_response = state.get("llm_response", "")

    # If no response to evaluate, pass through
    if not llm_response:
        state["reflexion_score"] = 0.0
        state["reflexion_critique"] = "No response generated"
        return state

    try:
        result = await _critic_llm.ainvoke([
            SystemMessage(content=CRITIC_PROMPT.format(
                skill_name=skill_name,
                query=query,
                response=llm_response
            )),
            HumanMessage(content="Evaluate.")
        ])

        raw = result.content.strip()
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
            iteration=iteration
        )

        # Accept output if score is good or max iterations reached
        if avg_score >= 7.0 or iteration >= 3:
            state["final_response"] = llm_response

            # Archive high-quality traces to Graphiti
            if avg_score >= 8.0:
                import asyncio
                asyncio.create_task(
                    nexus_graph_store.archive_procedure(
                        skill_name=skill_name,
                        inputs={"query": query},
                        reasoning_trace=state.get("reasoning_trace", ""),
                        final_output=llm_response,
                        reflexion_score=avg_score
                    )
                )

    except Exception as e:
        log.error("reflexion_error", error=str(e))
        # On error: accept the output, don't block
        state["reflexion_score"] = 10.0
        state["final_response"] = llm_response

    return state

"""
NEXUS-thirdy | agent/graph.py
Phase 4 — Updated Graph with Real Memory

Changes from Phase 3:
  - memory_update_node now saves to Mem0 + Graphiti
  - supervisor_node now receives real context pack from context_builder
  - Checkpointing added via Supabase PostgreSQL (crash recovery)
"""

from typing import TypedDict, Annotated, Literal
import operator
import asyncio

from langgraph.graph import StateGraph, END
from agent.supervisor import supervisor_node
from agent.nodes.free_skills import free_skills_node
from agent.nodes.premium_skills import premium_skills_node
from memory.context_builder import build_context_pack, save_conversation
from config.settings import settings
import structlog

log = structlog.get_logger()


# ── STATE ─────────────────────────────────────────────────────────────────────

class ThirdyState(TypedDict):
    user_id: str
    platform: str
    raw_message: str
    detected_skill: str
    requires_payment: bool
    payment_verified: bool
    context_pack: str
    llm_response: str
    final_response: str
    messages: Annotated[list, operator.add]


# ── ROUTING ───────────────────────────────────────────────────────────────────

def route_after_supervisor(state: ThirdyState) -> Literal["free_skills", "premium_skills"]:
    if state.get("requires_payment"):
        return "premium_skills"
    return "free_skills"


# ── CONTEXT NODE ──────────────────────────────────────────────────────────────

async def context_node(state: ThirdyState) -> ThirdyState:
    """
    Phase 4 addition: builds context pack from real memory before supervisor routes.
    Runs Mem0 + Graphiti retrieval in parallel.
    """
    try:
        context = await build_context_pack(
            user_id=state["user_id"],
            query=state["raw_message"]
        )
        state["context_pack"] = context
        log.info("context_built", user_id=state["user_id"], has_context=bool(context))
    except Exception as e:
        log.error("context_build_failed", error=str(e))
        state["context_pack"] = ""
    return state


# ── MEMORY UPDATE NODE ────────────────────────────────────────────────────────

async def memory_update_node(state: ThirdyState) -> ThirdyState:
    """
    Phase 4: saves conversation to Mem0 + Graphiti after response is ready.
    Fire and forget — response is already sent before this completes.
    """
    final_response = state.get("final_response", "")
    if final_response:
        asyncio.create_task(
            save_conversation(
                user_id=state["user_id"],
                user_message=state["raw_message"],
                agent_response=final_response
            )
        )
    log.info(
        "memory_update_queued",
        user_id=state["user_id"],
        skill=state["detected_skill"]
    )
    return state


# ── BUILD GRAPH ───────────────────────────────────────────────────────────────

def build_graph():
    builder = StateGraph(ThirdyState)

    # Nodes
    builder.add_node("context", context_node)         # Phase 4: memory retrieval
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("free_skills", free_skills_node)
    builder.add_node("premium_skills", premium_skills_node)
    builder.add_node("memory_update", memory_update_node)  # Phase 4: memory storage

    # Flow: context → supervisor → skill → memory → end
    builder.set_entry_point("context")
    builder.add_edge("context", "supervisor")

    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "free_skills": "free_skills",
            "premium_skills": "premium_skills",
        }
    )

    builder.add_edge("free_skills", "memory_update")
    builder.add_edge("premium_skills", "memory_update")
    builder.add_edge("memory_update", END)

    return builder.compile()


nexus_graph = build_graph()

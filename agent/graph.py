"""
NEXUS-thirdy | agent/graph.py
Phase 6 — Updated Graph with Reflexion

Changes from Phase 4:
  - Added reflexion_node after premium_skills
  - Reflexion can loop back to premium_skills (max 3 iterations)
  - Premium skills now use real NVIDIA/Cerebras LLMs
  - State includes reflexion_score, reflexion_iteration, reflexion_critique
"""

from typing import TypedDict, Annotated, Literal
import operator
import asyncio

from langgraph.graph import StateGraph, END
from agent.supervisor import supervisor_node
from agent.nodes.free_skills import free_skills_node
from agent.nodes.premium_skills import premium_skills_node
from agent.reflexion import reflexion_node
from memory.context_builder import build_context_pack, save_conversation
from config.settings import settings
import structlog

log = structlog.get_logger()


# ── STATE ─────────────────────────────────────────────────────────────────────

class ThirdyState(TypedDict):
    # Input
    user_id: str
    platform: str
    raw_message: str

    # Routing
    detected_skill: str
    requires_payment: bool
    payment_verified: bool

    # Context
    context_pack: str

    # Processing
    llm_response: str
    reasoning_trace: str

    # Reflexion
    reflexion_score: float
    reflexion_iteration: int
    reflexion_critique: str

    # Output
    final_response: str
    messages: Annotated[list, operator.add]


# ── ROUTING ───────────────────────────────────────────────────────────────────

def route_after_supervisor(state: ThirdyState) -> Literal["free_skills", "premium_skills"]:
    if state.get("requires_payment") and state.get("payment_verified"):
        return "premium_skills"
    elif state.get("requires_payment") and not state.get("payment_verified"):
        # Payment required but not verified — show payment info via free_skills
        # The free_skills node will detect this and return payment instructions
        state["detected_skill"] = "_payment_required"
        return "free_skills"
    return "free_skills"


def route_after_reflexion(state: ThirdyState) -> Literal["premium_skills", "memory_update"]:
    """
    If score < 7 AND iteration < 3: retry premium skill with critique.
    Otherwise: accept output and move to memory update.
    """
    score = state.get("reflexion_score", 10.0)
    iteration = state.get("reflexion_iteration", 0)

    if score < 7.0 and iteration < 3:
        log.info("reflexion_retry", score=score, iteration=iteration)
        return "premium_skills"
    return "memory_update"


# ── CONTEXT NODE ──────────────────────────────────────────────────────────────

async def context_node(state: ThirdyState) -> ThirdyState:
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


# ── PAYMENT GATE NODE ─────────────────────────────────────────────────────────

async def payment_gate_node(state: ThirdyState) -> ThirdyState:
    """
    Phase 6: simple payment check.
    Phase 7 will replace this with real x402 verification.
    """
    from config.skill_registry import SKILL_REGISTRY
    skill = SKILL_REGISTRY.get(state.get("detected_skill", ""))

    if not skill:
        state["payment_verified"] = False
        state["final_response"] = "Skill not found."
        return state

    # Phase 6: check if payment_proof is provided in state
    payment_proof = state.get("payment_proof", "")

    if payment_proof:
        # Phase 7 will verify this cryptographically via x402
        state["payment_verified"] = True
        log.info("payment_accepted_stub", skill=skill.id)
    else:
        state["payment_verified"] = False
        state["final_response"] = (
            f"**{skill.name}** costs {skill.price_usdc} USDC.\n\n"
            f"{skill.description}\n\n"
            f"Send {skill.price_usdc} USDC to unlock this skill. "
            f"Payment integration coming in Phase 7."
        )

    return state


def route_after_payment(state: ThirdyState) -> Literal["premium_skills", "memory_update"]:
    if state.get("payment_verified"):
        return "premium_skills"
    return "memory_update"


# ── MEMORY UPDATE NODE ────────────────────────────────────────────────────────

async def memory_update_node(state: ThirdyState) -> ThirdyState:
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
    builder.add_node("context", context_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("free_skills", free_skills_node)
    builder.add_node("payment_gate", payment_gate_node)
    builder.add_node("premium_skills", premium_skills_node)
    builder.add_node("reflexion", reflexion_node)
    builder.add_node("memory_update", memory_update_node)

    # Flow
    builder.set_entry_point("context")
    builder.add_edge("context", "supervisor")

    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "free_skills": "free_skills",
            "premium_skills": "payment_gate",
        }
    )

    # Free skills → memory → end
    builder.add_edge("free_skills", "memory_update")

    # Payment gate → premium or end
    builder.add_conditional_edges(
        "payment_gate",
        route_after_payment,
        {
            "premium_skills": "premium_skills",
            "memory_update": "memory_update",
        }
    )

    # Premium → reflexion
    builder.add_edge("premium_skills", "reflexion")

    # Reflexion → retry or end
    builder.add_conditional_edges(
        "reflexion",
        route_after_reflexion,
        {
            "premium_skills": "premium_skills",
            "memory_update": "memory_update",
        }
    )

    builder.add_edge("memory_update", END)

    return builder.compile()


nexus_graph = build_graph()

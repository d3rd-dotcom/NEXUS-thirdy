"""
NEXUS-thirdy | agent/graph.py
Phase 6 — LangGraph Brain

FIXED (C2): payment_gate_node now calls verify_payment() for real validation.
            Any non-empty string no longer bypasses payment.
FIXED (C3): payment_proof added to ThirdyState TypedDict.
FIXED (M8): route_after_supervisor is now a pure function — no state mutation,
            no magic "_payment_required" string. Returns "payment_gate" directly
            for premium skills so the conditional edge map is unambiguous.
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
    # FIXED (C3): payment_proof was missing from the TypedDict; all nodes that
    # read state.get("payment_proof") were implicitly relying on the dict having
    # this key set by the caller — which pinai.py and fetchai.py did NOT do.
    payment_proof: str

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


# ── ROUTING FUNCTIONS ─────────────────────────────────────────────────────────
# FIXED (M8): All routing functions are now pure — they read state but never
# mutate it. State mutation inside a routing function is an anti-pattern in
# LangGraph: the framework may call the function multiple times for graph
# introspection, and mutations would silently corrupt state each time.

def route_after_supervisor(
    state: ThirdyState,
) -> Literal["free_skills", "payment_gate"]:
    """
    Route premium skills through the payment gate; free skills bypass it.

    FIXED (M8): Previous implementation mutated state["detected_skill"] to the
    magic string "_payment_required" (not present in SKILL_REGISTRY) and returned
    "free_skills", sending premium requests to the wrong node. Now returns
    "payment_gate" directly, and the conditional edge map is explicit.
    """
    if state.get("requires_payment"):
        return "payment_gate"
    return "free_skills"


def route_after_payment(
    state: ThirdyState,
) -> Literal["premium_skills", "memory_update"]:
    """Route to premium execution on success; skip to memory update on failure."""
    if state.get("payment_verified"):
        return "premium_skills"
    return "memory_update"


def route_after_reflexion(
    state: ThirdyState,
) -> Literal["premium_skills", "memory_update"]:
    """
    Retry if score < 7 and we have not hit 3 iterations yet.
    Otherwise accept the output and proceed to memory update.
    """
    score = state.get("reflexion_score", 10.0)
    iteration = state.get("reflexion_iteration", 0)
    if score < 7.0 and iteration < 3:
        log.info("reflexion_retry", score=score, iteration=iteration)
        return "premium_skills"
    return "memory_update"


# ── CONTEXT NODE ──────────────────────────────────────────────────────────────

async def context_node(state: ThirdyState) -> ThirdyState:
    """Retrieve hybrid memory context before routing."""
    try:
        context = await build_context_pack(
            user_id=state["user_id"],
            query=state["raw_message"],
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
    Validate x402 payment proof before allowing premium skill execution.

    FIXED (C2): The previous implementation accepted any non-empty string as a
    valid payment proof (stub logic leaked into production path). This node now
    delegates to verify_payment() in payments/x402_middleware.py which enforces
    either real x402 cryptographic verification (X402_VERIFY_PAYMENTS=true) or
    a clearly-marked stub prefix (stub_test_*) in development mode.
    """
    from config.skill_registry import SKILL_REGISTRY
    from payments.x402_middleware import verify_payment, build_payment_required_response
    from payments.wallet import get_wallet_address

    skill_id = state.get("detected_skill", "")
    skill = SKILL_REGISTRY.get(skill_id)

    if not skill:
        state["payment_verified"] = False
        state["final_response"] = "Skill not found."
        return state

    payment_proof = state.get("payment_proof", "")

    # FIXED (C2): Delegate to x402_middleware — never accept arbitrary strings.
    is_verified, reason = await verify_payment(
        skill_id=skill.id,
        payment_proof=payment_proof,
    )

    if is_verified:
        state["payment_verified"] = True
        log.info("payment_gate_passed", skill=skill.id, reason=reason)
    else:
        state["payment_verified"] = False
        wallet_address = await get_wallet_address()
        state["final_response"] = (
            f"**{skill.name}** costs {skill.price_usdc} USDC.\n\n"
            f"{skill.description}\n\n"
            f"Send {skill.price_usdc} USDC to `{wallet_address}` on "
            f"{settings.X402_NETWORK}, then retry with your payment proof in "
            f"the `payment_proof` field.\n"
            f"_(Reason: {reason})_"
        )
        log.info("payment_gate_rejected", skill=skill.id, reason=reason)

    return state


# ── MEMORY UPDATE NODE ────────────────────────────────────────────────────────

async def memory_update_node(state: ThirdyState) -> ThirdyState:
    """Save conversation to Mem0 + Graphiti after the response is ready."""
    final_response = state.get("final_response", "")
    if final_response:
        asyncio.create_task(
            save_conversation(
                user_id=state["user_id"],
                user_message=state["raw_message"],
                agent_response=final_response,
            )
        )
    log.info(
        "memory_update_queued",
        user_id=state["user_id"],
        skill=state["detected_skill"],
    )
    return state


# ── BUILD GRAPH ───────────────────────────────────────────────────────────────

def build_graph():
    builder = StateGraph(ThirdyState)

    builder.add_node("context", context_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("free_skills", free_skills_node)
    # FIXED (M8): payment_gate is now an explicit node in the graph rather than
    # a detour through free_skills with a magic detected_skill string.
    builder.add_node("payment_gate", payment_gate_node)
    builder.add_node("premium_skills", premium_skills_node)
    builder.add_node("reflexion", reflexion_node)
    builder.add_node("memory_update", memory_update_node)

    builder.set_entry_point("context")
    builder.add_edge("context", "supervisor")

    # FIXED (M8): Edge map now uses the actual return values of route_after_supervisor.
    # Previously the map had {"premium_skills": "payment_gate"} with the function
    # returning "premium_skills" — a confusing indirection. Now the function returns
    # "payment_gate" and the map is a straight 1-to-1.
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "free_skills": "free_skills",
            "payment_gate": "payment_gate",
        },
    )

    builder.add_edge("free_skills", "memory_update")

    builder.add_conditional_edges(
        "payment_gate",
        route_after_payment,
        {
            "premium_skills": "premium_skills",
            "memory_update": "memory_update",
        },
    )

    builder.add_edge("premium_skills", "reflexion")

    builder.add_conditional_edges(
        "reflexion",
        route_after_reflexion,
        {
            "premium_skills": "premium_skills",
            "memory_update": "memory_update",
        },
    )

    builder.add_edge("memory_update", END)

    return builder.compile()


nexus_graph = build_graph()

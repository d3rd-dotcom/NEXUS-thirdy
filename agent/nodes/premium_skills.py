"""
NEXUS-thirdy | agent/nodes/premium_skills.py
Phase 3 — Premium Skills Node (Stub)

Premium skills are stubs in Phase 3.
They will be fully implemented in Phase 6 after:
  - Memory layer is installed (Phase 4)
  - Reflexion node is built (Phase 6)
  - x402 payments are set up (Phase 7)

For now: if a premium skill is called, return a payment prompt.
This lets the routing logic work end-to-end without breaking.
"""

from config.skill_registry import SKILL_REGISTRY
from config.settings import settings
import structlog

log = structlog.get_logger()


async def premium_skills_node(state: dict) -> dict:
    """
    Phase 3 stub — returns payment info for premium skills.
    Full implementation in Phase 6.
    """
    skill_id = state.get("detected_skill", "")
    skill = SKILL_REGISTRY.get(skill_id)

    if not skill:
        state["final_response"] = "Skill not found."
        return state

    # Phase 3: premium skills show payment instructions
    # Phase 6: this will run NVIDIA NIM / Cerebras with full Reflexion
    state["final_response"] = (
        f"**{skill.name}** is a premium skill ({skill.price_usdc} USDC).\n\n"
        f"{skill.description}\n\n"
        f"Payment integration coming in Phase 7. "
        f"Stay tuned — NEXUS-thirdy is still being built!"
    )

    log.info("premium_skill_stub", skill=skill_id, price=skill.price_usdc)
    return state

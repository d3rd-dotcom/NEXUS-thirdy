"""
NEXUS-thirdy | agent/state_factory.py
Centralised initial-state factory.

FIXED (C3, C4): Previously the initial-state dict was copy-pasted in four places
(platforms/pinai.py, platforms/fetchai.py, api/server.py process_chat,
api/server.py agent_skill_call) with inconsistent keys. Missing keys like
`payment_proof`, `reasoning_trace`, `reflexion_score`, `reflexion_iteration`,
and `reflexion_critique` caused KeyError crashes inside LangGraph nodes for
any message that arrived via PIN AI or Fetch.AI.

All callers must use make_initial_state() — never build the dict manually.
"""


def make_initial_state(
    user_id: str,
    message: str,
    platform: str,
    payment_proof: str = "",
) -> dict:
    """
    Return a fully-populated initial ThirdyState dict.

    Every key required by the LangGraph TypedDict is present and set to a
    safe default. No node will ever receive a KeyError from a missing key.

    Args:
        user_id:       Sanitized user identifier (call sanitize_user_id() first).
        message:       Raw user message string (will be stripped).
        platform:      Source platform string, e.g. "pinai", "fetchai", "webhook".
        payment_proof: Optional x402 payment proof header value. Default empty.

    Returns:
        dict compatible with ThirdyState TypedDict.
    """
    return {
        # ── Input ──────────────────────────────────────────────────────────────
        "user_id": user_id,
        "platform": platform,
        "raw_message": message.strip() if message else "",

        # ── Routing ────────────────────────────────────────────────────────────
        "detected_skill": "",
        "requires_payment": False,
        "payment_verified": False,
        # FIXED (C3): payment_proof was absent in pinai.py and fetchai.py builds,
        # causing the payment_gate_node to always see an empty proof string even
        # when the caller provided one.
        "payment_proof": payment_proof,

        # ── Context ────────────────────────────────────────────────────────────
        "context_pack": "",

        # ── Processing ─────────────────────────────────────────────────────────
        "llm_response": "",
        # FIXED (C3): reasoning_trace was missing in pinai.py / fetchai.py builds,
        # causing reflexion_node to log an empty trace even on successful runs.
        "reasoning_trace": "",

        # ── Reflexion ──────────────────────────────────────────────────────────
        # FIXED (C3): All three reflexion keys were absent in pinai / fetchai builds.
        # reflexion_node increments reflexion_iteration before reading it, so a
        # missing key caused an immediate KeyError on the first premium skill call.
        "reflexion_score": 0.0,
        "reflexion_iteration": 0,
        "reflexion_critique": "",

        # ── Output ─────────────────────────────────────────────────────────────
        "final_response": "",
        "messages": [],
    }

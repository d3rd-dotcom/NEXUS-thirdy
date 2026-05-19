"""
NEXUS-thirdy | payments/x402_middleware.py
Phase 7 — x402 Payment Verification

x402 is the open payment protocol by Coinbase + Cloudflare.
HTTP 402 = "Payment Required" — used for machine-to-machine micropayments.

How it works:
  1. Client calls a premium skill endpoint
  2. Server returns 402 with payment instructions
  3. Client sends USDC on Base
  4. Client retries with payment proof in X-Payment header
  5. Server verifies proof cryptographically and executes skill

FIXED (C2): Complete payment bypass removed.

Previous behaviour: when the x402 facilitator was not configured (i.e. CDP
credentials absent), verify_payment() returned (True, "stub_accepted") for
ANY non-empty payment_proof string. This meant any caller who simply sent
`"payment_proof": "hello"` got free access to all premium skills.

New behaviour:
  X402_VERIFY_PAYMENTS=true  → Full cryptographic verification via the
                               Coinbase CDP facilitator. Required for
                               production.
  X402_VERIFY_PAYMENTS=false → Stub mode for local dev and CI. ONLY proofs
                               prefixed with "stub_test_" are accepted.
                               Any other string is explicitly rejected with
                               reason "invalid_proof_format_stub_mode".

This means the bypass is closed in both modes. A developer testing locally
must explicitly use a "stub_test_" prefixed string — they cannot accidentally
discover the bypass by sending any arbitrary value.
"""

from config.settings import settings
from config.skill_registry import get_skill
import structlog

log = structlog.get_logger()

# ── CONSTANTS ─────────────────────────────────────────────────────────────────

# FIXED (C2): Explicit sentinel prefix for stub-mode testing.
# Must be deliberate — cannot be guessed by accident or by automated scanners.
_STUB_TEST_PREFIX = "stub_test_"

_facilitator = None


# ── LAZY FACILITATOR CLIENT ───────────────────────────────────────────────────

def _get_facilitator():
    """
    Lazy initialisation of the x402 Coinbase CDP facilitator client.
    Returns None if CDP credentials are not configured.
    """
    global _facilitator
    if _facilitator is not None:
        return _facilitator

    if not settings.CDP_API_KEY_NAME:
        return None

    try:
        from x402.client import FacilitatorClient
        _facilitator = FacilitatorClient(
            api_key_name=settings.CDP_API_KEY_NAME,
            api_key_secret=settings.CDP_API_KEY_SECRET,
        )
        log.info("x402_facilitator_initialized")
        return _facilitator
    except Exception as e:
        log.error("x402_facilitator_init_failed", error=str(e))
        return None


# ── PAYMENT VERIFICATION ──────────────────────────────────────────────────────

async def verify_payment(
    skill_id: str,
    payment_proof: str,
    user_address: str = "",
) -> tuple[bool, str]:
    """
    Verify an x402 payment proof for a premium skill.

    Returns (is_verified: bool, reason: str).

    FIXED (C2): Previously accepted ANY non-empty string when the facilitator
    was not configured — a silent complete bypass. Now enforces strict
    validation in both production and stub mode.

    Args:
        skill_id:      Skill being accessed (used to look up expected price).
        payment_proof: The X-Payment header value from the client.
        user_address:  Optional payer address for per-user spend limit checks.
    """
    skill = get_skill(skill_id)
    if not skill:
        return False, "skill_not_found"

    # Free skills need no proof
    if skill.price_usdc == 0:
        return True, "free_skill"

    # No proof provided at all
    if not payment_proof:
        return False, "no_payment_proof"

    # ── Branch on verification mode ───────────────────────────────────────────

    if settings.X402_VERIFY_PAYMENTS:
        return await _verify_production(skill, payment_proof, user_address)
    else:
        return _verify_stub(skill_id, payment_proof)


async def _verify_production(skill, payment_proof: str, user_address: str) -> tuple[bool, str]:
    """
    Full cryptographic x402 verification via Coinbase CDP facilitator.
    Used when X402_VERIFY_PAYMENTS=true.
    """
    facilitator = _get_facilitator()

    if not facilitator:
        # Credentials missing but production mode requested — hard failure.
        # Accepting payments without verification would be financially dangerous.
        log.error(
            "x402_production_mode_but_no_facilitator",
            hint="Set CDP_API_KEY_NAME and CDP_API_KEY_SECRET, or set X402_VERIFY_PAYMENTS=false for dev",
        )
        return False, "facilitator_not_configured"

    try:
        from x402.types import PaymentPayload

        payload = PaymentPayload.from_header(payment_proof)

        result = await facilitator.verify(
            payload=payload,
            expected_amount=skill.price_usdc,
            expected_currency="USDC",
            expected_network=settings.X402_NETWORK,
            recipient=settings.AGENT_WALLET_ADDRESS,
        )

        if result.valid:
            log.info(
                "payment_verified_production",
                skill=skill.id,
                amount=skill.price_usdc,
                payer=user_address,
            )
            return True, "verified"
        else:
            log.warning(
                "payment_invalid_production",
                skill=skill.id,
                reason=result.reason,
            )
            return False, result.reason

    except Exception as e:
        log.error("payment_verify_exception", error=str(e))
        return False, "verification_error"


def _verify_stub(skill_id: str, payment_proof: str) -> tuple[bool, str]:
    """
    Stub verification for local development and CI.
    Used when X402_VERIFY_PAYMENTS=false (default).

    FIXED (C2): Previously returned (True, "stub_accepted") for ANY non-empty
    string. Now ONLY accepts proofs that begin with the "stub_test_" sentinel.
    An attacker sending `"payment_proof": "anything"` is rejected with a
    clear reason string rather than silently granted access.
    """
    if payment_proof.startswith(_STUB_TEST_PREFIX):
        log.warning(
            "stub_payment_accepted",
            skill=skill_id,
            proof_preview=payment_proof[:40],
            warning="X402_VERIFY_PAYMENTS=false — stub mode active. Set true in production.",
        )
        return True, "stub_accepted"

    # FIXED (C2): Explicit rejection of all non-stub strings in stub mode.
    log.warning(
        "stub_payment_rejected_invalid_format",
        skill=skill_id,
        hint=(
            f"In stub mode, payment_proof must start with '{_STUB_TEST_PREFIX}'. "
            "Example: 'stub_test_local_dev_run'. "
            "For real payments set X402_VERIFY_PAYMENTS=true."
        ),
    )
    return False, "invalid_proof_format_stub_mode"


# ── PAYMENT REQUIRED RESPONSE ─────────────────────────────────────────────────

def build_payment_required_response(skill_id: str, agent_wallet: str) -> dict:
    """
    Build the 402 Payment Required response body.
    Tells the caller exactly what to pay, on which network, and to whom.
    """
    skill = get_skill(skill_id)
    if not skill:
        return {}

    return {
        "error": "payment_required",
        "skill": skill_id,
        "skill_name": skill.name,
        "amount_usdc": skill.price_usdc,
        "currency": "USDC",
        "network": settings.X402_NETWORK,
        "recipient": agent_wallet,
        "description": skill.description,
        "instructions": (
            f"Send {skill.price_usdc} USDC on {settings.X402_NETWORK} "
            f"to {agent_wallet}, then retry with the X-Payment header "
            f"containing your payment proof."
        ),
    }

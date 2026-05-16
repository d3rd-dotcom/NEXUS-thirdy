"""
NEXUS-thirdy | payments/x402_middleware.py
Phase 7 — x402 Payment Verification

x402 is the open payment protocol by Coinbase + Cloudflare.
HTTP 402 = "Payment Required" — the long-unused status code, now the standard
for machine-to-machine micropayments.

How it works:
  1. Client calls a premium skill endpoint
  2. Server returns 402 with payment instructions
  3. Client sends USDC on Base
  4. Client retries with payment proof in header
  5. Server verifies proof and executes skill

Free tier: 1000 transactions/month via Coinbase CDP facilitator.
Network: Base (lowest gas fees).
"""

from config.settings import settings
from config.skill_registry import get_skill
import structlog

log = structlog.get_logger()

_facilitator = None


def get_facilitator():
    """Lazy init of x402 facilitator client."""
    global _facilitator
    if _facilitator is not None:
        return _facilitator

    if not settings.CDP_API_KEY_NAME:
        return None

    try:
        from x402.client import FacilitatorClient
        _facilitator = FacilitatorClient(
            api_key_name=settings.CDP_API_KEY_NAME,
            api_key_secret=settings.CDP_API_KEY_SECRET
        )
        log.info("x402_facilitator_initialized")
        return _facilitator
    except Exception as e:
        log.error("x402_init_failed", error=str(e))
        return None


async def verify_payment(
    skill_id: str,
    payment_proof: str,
    user_address: str = ""
) -> tuple[bool, str]:
    """
    Verify x402 payment proof for a premium skill.
    Returns (is_verified: bool, reason: str).

    payment_proof: the X-Payment header value from the client
    user_address: optional — for per-user spend limit checks
    """
    skill = get_skill(skill_id)
    if not skill:
        return False, "skill_not_found"

    if skill.price_usdc == 0:
        return True, "free_skill"

    if not payment_proof:
        return False, "no_payment_proof"

    facilitator = get_facilitator()
    if not facilitator:
        # x402 not configured — Phase 7 stub, accept with proof present
        log.warning("x402_not_configured_accepting_stub", skill=skill_id)
        return True, "stub_accepted"

    try:
        from x402.types import PaymentPayload
        payload = PaymentPayload.from_header(payment_proof)

        result = await facilitator.verify(
            payload=payload,
            expected_amount=skill.price_usdc,
            expected_currency="USDC",
            expected_network=settings.X402_NETWORK,
            recipient=settings.AGENT_WALLET_ADDRESS
        )

        if result.valid:
            log.info(
                "payment_verified",
                skill=skill_id,
                amount=skill.price_usdc,
                payer=user_address
            )
            return True, "verified"
        else:
            log.warning("payment_invalid", skill=skill_id, reason=result.reason)
            return False, result.reason

    except Exception as e:
        log.error("payment_verify_failed", error=str(e))
        return False, "verification_error"


def build_payment_required_response(skill_id: str, agent_wallet: str) -> dict:
    """
    Build the 402 Payment Required response body.
    Tells the client exactly what to pay, where, and how.
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
            f"to {agent_wallet}, then retry with X-Payment header."
        )
    }

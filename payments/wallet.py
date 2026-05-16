"""
NEXUS-thirdy | payments/wallet.py
Phase 7 — Coinbase AgentKit MPC Wallet

Creates and manages NEXUS-thirdy's on-chain wallet.
MPC = Multi-Party Computation — no single private key that can be stolen.
Network: Base (lowest fees in the x402 ecosystem).

Run scripts/init_wallet.py once to create the wallet.
The wallet address is then stored as AGENT_WALLET_ADDRESS env var.
"""

from config.settings import settings
import structlog

log = structlog.get_logger()

_wallet = None


def get_wallet():
    """
    Lazy initialization of AgentKit wallet.
    Returns wallet instance or None if AgentKit not configured.
    """
    global _wallet

    if _wallet is not None:
        return _wallet

    if not settings.CDP_API_KEY_NAME or not settings.CDP_API_KEY_SECRET:
        log.warning("agentkit_not_configured", reason="CDP credentials missing")
        return None

    try:
        from coinbase_agentkit import AgentKit, AgentKitConfig

        kit = AgentKit(AgentKitConfig(
            cdp_api_key_name=settings.CDP_API_KEY_NAME,
            cdp_api_key_private_key=settings.CDP_API_KEY_SECRET,
            network_id=settings.X402_NETWORK
        ))

        _wallet = kit
        log.info("agentkit_initialized", network=settings.X402_NETWORK)
        return _wallet

    except Exception as e:
        log.error("agentkit_init_failed", error=str(e))
        return None


async def get_wallet_address() -> str:
    """Returns the agent's wallet address."""
    if settings.AGENT_WALLET_ADDRESS:
        return settings.AGENT_WALLET_ADDRESS

    wallet = get_wallet()
    if not wallet:
        return ""

    try:
        address = wallet.wallet.default_address.address_id
        return address
    except Exception as e:
        log.error("wallet_address_failed", error=str(e))
        return ""


async def get_balance() -> dict:
    """Returns current USDC balance on Base."""
    wallet = get_wallet()
    if not wallet:
        return {"usdc": 0, "error": "wallet_not_configured"}

    try:
        balances = wallet.wallet.balances()
        return {
            "usdc": float(balances.get("usdc", 0)),
            "eth": float(balances.get("eth", 0)),
            "network": settings.X402_NETWORK
        }
    except Exception as e:
        log.error("balance_check_failed", error=str(e))
        return {"usdc": 0, "error": str(e)}

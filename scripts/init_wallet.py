"""
NEXUS-thirdy | scripts/init_wallet.py
Phase 7 — One-time wallet initialization script

Run this ONCE locally to create NEXUS-thirdy's MPC wallet on Base.
Copy the wallet address to Render as AGENT_WALLET_ADDRESS env var.

Usage:
  python scripts/init_wallet.py
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from config.settings import settings


def init_wallet():
    if not settings.CDP_API_KEY_NAME or not settings.CDP_API_KEY_SECRET:
        print("❌ CDP_API_KEY_NAME and CDP_API_KEY_SECRET not set in .env")
        print("Get your free Coinbase Developer Platform credentials at:")
        print("https://portal.cdp.coinbase.com/")
        return

    try:
        from coinbase_agentkit import AgentKit, AgentKitConfig

        print(f"Initializing wallet on network: {settings.X402_NETWORK}")

        kit = AgentKit(AgentKitConfig(
            cdp_api_key_name=settings.CDP_API_KEY_NAME,
            cdp_api_key_private_key=settings.CDP_API_KEY_SECRET,
            network_id=settings.X402_NETWORK
        ))

        address = kit.wallet.default_address.address_id
        print(f"\n✅ Wallet created successfully!")
        print(f"Wallet address: {address}")
        print(f"\nAdd this to Render environment variables:")
        print(f"AGENT_WALLET_ADDRESS={address}")
        print(f"\nAlso add to your .env file:")
        print(f"AGENT_WALLET_ADDRESS={address}")

    except ImportError:
        print("❌ coinbase-agentkit not installed.")
        print("Run: pip install coinbase-agentkit")
    except Exception as e:
        print(f"❌ Wallet init failed: {e}")


if __name__ == "__main__":
    init_wallet()

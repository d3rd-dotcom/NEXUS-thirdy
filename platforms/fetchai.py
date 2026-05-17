"""
NEXUS-thirdy | platforms/fetchai.py
Phase 9 — Fetch.AI Agentverse Connection

Registers NEXUS-thirdy as a uAgent on Fetch.AI Agentverse.
Other agents on the Fetch.AI network can discover and hire NEXUS-thirdy
for tasks automatically — agent-to-agent economy.

Free tier: unlimited agent registrations on Agentverse.
FET token rewards for active agents with high interaction counts.

Setup:
  1. Sign up at agentverse.ai
  2. Get your AGENTVERSE_API_KEY from the dashboard
  3. Add AGENTVERSE_API_KEY to Render environment variables
  4. This module registers and runs NEXUS-thirdy as a uAgent
"""

import asyncio
import httpx
from config.settings import settings
from agent.graph import nexus_graph
from security.validators import validate_input, sanitize_user_id
import structlog

log = structlog.get_logger()

AGENTVERSE_URL = "https://agentverse.ai"
ALMANAC_URL = "https://almanac.agentverse.ai"


class FetchAIConnector:
    """
    Connects NEXUS-thirdy to Fetch.AI Agentverse.
    Handles registration, message polling, and replies.
    """

    def __init__(self):
        self._api_key = settings.FETCHAI_API_KEY if hasattr(settings, 'FETCHAI_API_KEY') else ""
        self._agent_address = ""
        self._enabled = bool(self._api_key)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }

    async def register(self) -> bool:
        """Register NEXUS-thirdy on Agentverse."""
        if not self._enabled:
            return False

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{AGENTVERSE_URL}/api/v1/agents",
                    headers=self._headers(),
                    json={
                        "name": "NEXUS-thirdy",
                        "readme": self._build_readme(),
                        "endpoint": f"https://nexus-thirdy.onrender.com/webhook",
                    }
                )
                if r.status_code in (200, 201):
                    data = r.json()
                    self._agent_address = data.get("address", "")
                    log.info("fetchai_registered", address=self._agent_address)
                    return True
                log.warning("fetchai_register_error", status=r.status_code, body=r.text[:200])
                return False
        except Exception as e:
            log.error("fetchai_register_failed", error=str(e))
            return False

    def _build_readme(self) -> str:
        return """# NEXUS-thirdy

Intelligent AI agent with hybrid memory, crypto intelligence, and autonomous payments.

## Capabilities
- Crypto price checks and market analysis
- DeFi yield finding
- Sentiment scanning
- Portfolio tracking
- General AI assistance

## How to interact
Send a message with your query. Premium skills require USDC payment via x402.

## Endpoint
https://nexus-thirdy.onrender.com/agent
"""

    async def poll_and_process(self) -> None:
        """Poll for messages from Fetch.AI and process them."""
        if not self._enabled:
            return

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{AGENTVERSE_URL}/api/v1/agents/messages",
                    headers=self._headers()
                )
                if r.status_code != 200:
                    return

                messages = r.json().get("messages", [])
                for msg in messages:
                    sender = msg.get("sender", "unknown")
                    content = msg.get("content", "")
                    msg_id = msg.get("id", "")

                    if not content:
                        continue

                    user_id = sanitize_user_id(f"fetchai_{sender}")
                    validation = validate_input(content, user_id=user_id)
                    if not validation.is_valid:
                        continue

                    # Process through LangGraph
                    initial_state = {
                        "user_id": user_id,
                        "platform": "fetchai",
                        "raw_message": validation.sanitized or content,
                        "detected_skill": "",
                        "requires_payment": False,
                        "payment_verified": False,
                        "payment_proof": "",
                        "context_pack": "",
                        "llm_response": "",
                        "reasoning_trace": "",
                        "reflexion_score": 0.0,
                        "reflexion_iteration": 0,
                        "reflexion_critique": "",
                        "final_response": "",
                        "messages": []
                    }

                    final_state = await nexus_graph.ainvoke(initial_state)
                    response = final_state.get("final_response", "")

                    if response:
                        await self._reply(sender, response, msg_id)

        except Exception as e:
            log.error("fetchai_poll_failed", error=str(e))

    async def _reply(self, recipient: str, content: str, msg_id: str) -> None:
        """Send reply to a Fetch.AI agent."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{AGENTVERSE_URL}/api/v1/agents/messages",
                    headers=self._headers(),
                    json={
                        "recipient": recipient,
                        "content": content,
                        "reply_to": msg_id
                    }
                )
                log.info("fetchai_replied", recipient=recipient[:20])
        except Exception as e:
            log.error("fetchai_reply_failed", error=str(e))


# Singleton
fetchai_connector = FetchAIConnector()


async def fetchai_polling_loop():
    """
    Background task for Fetch.AI polling.
    Launched alongside PIN AI polling in the FastAPI lifespan.
    Silently disabled if FETCHAI_API_KEY not set.
    """
    if not fetchai_connector._enabled:
        log.info("fetchai_disabled", reason="FETCHAI_API_KEY not set — skipping")
        return

    log.info("fetchai_polling_started")

    # Register on startup
    await fetchai_connector.register()

    while True:
        try:
            await fetchai_connector.poll_and_process()
        except Exception as e:
            log.error("fetchai_loop_error", error=str(e))
        await asyncio.sleep(30)

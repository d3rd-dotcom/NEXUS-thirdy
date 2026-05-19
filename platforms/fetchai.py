"""
NEXUS-thirdy | platforms/fetchai.py
Phase 9 — Fetch.AI Agentverse Connection

Registers NEXUS-thirdy as a uAgent on Fetch.AI Agentverse.
Other agents on the Fetch.AI network can discover and hire NEXUS-thirdy
for tasks automatically — agent-to-agent economy.

Free tier: unlimited agent registrations on Agentverse.

Setup:
  1. Sign up at agentverse.ai
  2. Get your API key from the dashboard
  3. Add FETCHAI_API_KEY to Render environment variables
  4. This module registers and polls NEXUS-thirdy as a uAgent

FIXED (C3, C4): poll_and_process() now uses make_initial_state() to build
                the LangGraph initial state. The previous inline dict was
                missing payment_proof, reasoning_trace, reflexion_score,
                reflexion_iteration, and reflexion_critique — causing KeyError
                crashes inside reflexion_node and premium_skills_node for
                any Fetch.AI message that triggered a premium skill.
"""

import asyncio
import httpx
from config.settings import settings
from agent.graph import nexus_graph
from agent.state_factory import make_initial_state  # FIXED (C3, C4)
from security.validators import validate_input, sanitize_user_id
import structlog

log = structlog.get_logger()

AGENTVERSE_URL = "https://agentverse.ai"


class FetchAIConnector:
    """
    Connects NEXUS-thirdy to Fetch.AI Agentverse.
    Handles registration, message polling, and replies.
    Silently disabled when FETCHAI_API_KEY is not set.
    """

    def __init__(self):
        self._api_key = settings.FETCHAI_API_KEY
        self._agent_address = ""
        self._enabled = bool(self._api_key)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def register(self) -> bool:
        """Register NEXUS-thirdy on Agentverse at startup."""
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
                        "endpoint": "https://nexus-thirdy.onrender.com/webhook",
                    },
                )
                if r.status_code in (200, 201):
                    data = r.json()
                    self._agent_address = data.get("address", "")
                    log.info("fetchai_registered", address=self._agent_address)
                    return True
                log.warning(
                    "fetchai_register_error",
                    status=r.status_code,
                    body=r.text[:200],
                )
                return False
        except Exception as e:
            log.error("fetchai_register_failed", error=str(e))
            return False

    def _build_readme(self) -> str:
        return (
            "# NEXUS-thirdy\n\n"
            "Intelligent AI agent with hybrid memory, crypto intelligence, "
            "and autonomous payments.\n\n"
            "## Capabilities\n"
            "- Crypto price checks and market analysis\n"
            "- DeFi yield finding\n"
            "- Sentiment scanning\n"
            "- Portfolio tracking\n"
            "- General AI assistance\n\n"
            "## Endpoint\n"
            "https://nexus-thirdy.onrender.com/agent\n"
        )

    async def poll_and_process(self) -> None:
        """
        Poll Agentverse for pending messages and process each one through
        the full NEXUS-thirdy LangGraph.

        FIXED (C3, C4): Initial state is now built via make_initial_state()
        instead of an inline dict. The previous inline dict was missing five
        keys required by ThirdyState, causing KeyError crashes when reflexion
        or premium skill nodes accessed those missing keys.
        """
        if not self._enabled:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{AGENTVERSE_URL}/api/v1/agents/messages",
                    headers=self._headers(),
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

                    # Sanitize and validate before touching the graph
                    user_id = sanitize_user_id(f"fetchai_{sender}")
                    validation = validate_input(content, user_id=user_id)
                    if not validation.is_valid:
                        log.warning(
                            "fetchai_message_rejected",
                            user_id=user_id,
                            reason=validation.reason,
                        )
                        continue

                    # FIXED (C3, C4): Factory guarantees all 15 ThirdyState
                    # keys are present — no more KeyError in downstream nodes
                    initial_state = make_initial_state(
                        user_id=user_id,
                        message=validation.sanitized or content,
                        platform="fetchai",
                    )

                    try:
                        final_state = await nexus_graph.ainvoke(initial_state)
                        response = final_state.get("final_response", "")
                    except Exception as e:
                        log.error(
                            "fetchai_graph_error",
                            user_id=user_id,
                            error=str(e),
                        )
                        response = ""

                    if response:
                        await self._reply(sender, response, msg_id)

        except Exception as e:
            log.error("fetchai_poll_failed", error=str(e))

    async def _reply(self, recipient: str, content: str, msg_id: str) -> None:
        """Send a reply to a Fetch.AI agent."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{AGENTVERSE_URL}/api/v1/agents/messages",
                    headers=self._headers(),
                    json={
                        "recipient": recipient,
                        "content": content,
                        "reply_to": msg_id,
                    },
                )
                log.info("fetchai_replied", recipient=recipient[:30])
        except Exception as e:
            log.error("fetchai_reply_failed", error=str(e))


# Singleton
fetchai_connector = FetchAIConnector()


async def fetchai_polling_loop():
    """
    Background task — launched alongside PIN AI polling in the FastAPI
    lifespan context. Silently exits if FETCHAI_API_KEY is not set so the
    server starts cleanly in environments without Fetch.AI configured.
    """
    if not fetchai_connector._enabled:
        log.info("fetchai_disabled", reason="FETCHAI_API_KEY not set — skipping")
        return

    log.info("fetchai_polling_started")

    # Register with Agentverse on startup
    await fetchai_connector.register()

    while True:
        try:
            await fetchai_connector.poll_and_process()
        except Exception as e:
            log.error("fetchai_loop_error", error=str(e))
        await asyncio.sleep(30)

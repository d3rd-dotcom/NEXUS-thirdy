"""
NEXUS-thirdy | platforms/pinai.py
Phase 5 — PIN AI Connection (Corrected)

Uses the official PIN AI SDK API endpoints:
  POST /api/sdk/poll_messages   ← fetch unread messages
  POST /api/sdk/reply_message   ← send a reply
Auth header: x-api-key (not Bearer)

Runs as a background asyncio task — no separate process, no CMD window.
Starts automatically when FastAPI server starts.
"""

import asyncio
import httpx
from config.settings import settings
from agent.graph import nexus_graph
import structlog

log = structlog.get_logger()

# ── CONSTANTS ─────────────────────────────────────────────────────────────────

HEADERS = {
    "x-api-key": settings.PINAI_API_KEY,
    "Content-Type": "application/json"
}

POLL_INTERVAL = 5        # seconds between polls
HEARTBEAT_EVERY = 6      # send heartbeat every N polls (~30 seconds)

# Tracks last seen timestamp to avoid reprocessing old messages
_last_timestamp: int = 0

# Tracks sessions already greeted
_greeted_sessions: set[str] = set()


# ── API CALLS ─────────────────────────────────────────────────────────────────

async def poll_messages() -> list[dict]:
    """
    Fetch unread messages for NEXUS-thirdy since last poll.
    Uses PIN AI's poll_messages endpoint.
    """
    global _last_timestamp

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{settings.PINAI_API_URL}/api/sdk/poll_messages",
                headers=HEADERS,
                json={
                    "agent_id": (settings.PINAI_AGENT_ID),
                    "since_timestamp": _last_timestamp,
                    "sender": "user"   # Only fetch user messages, not our own replies
                }
            )
            if r.status_code == 200:
                messages = r.json()
                if messages:
                    # Update timestamp to latest message so we don't re-fetch
                    _last_timestamp = max(m.get("timestamp", 0) for m in messages)
                return messages
            else:
                log.warning("pinai_poll_error", status=r.status_code, body=r.text[:200])
                return []
    except Exception as e:
        log.error("pinai_poll_failed", error=str(e))
        return []


async def reply_message(session_id: str, persona_id: int, content: str) -> bool:
    """
    Send a reply to a user in a specific session.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{settings.PINAI_API_URL}/api/sdk/reply_message",
                headers=HEADERS,
                json={
                    "agent_id": (settings.PINAI_AGENT_ID),
                    "persona_id": persona_id,
                    "content": content,
                    "media_type": None,
                    "media_url": None,
                    "meta_data": {}
                },
                params={"session_id": session_id}
            )
            if r.status_code == 200:
                return True
            log.warning("pinai_reply_error", status=r.status_code, body=r.text[:200])
            return False
    except Exception as e:
        log.error("pinai_reply_failed", session=session_id, error=str(e))
        return False


async def get_persona(session_id: str) -> dict:
    """
    Get user persona info for a session.
    Useful for personalizing responses with user's name.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{settings.PINAI_API_URL}/api/sdk/get_persona_by_session",
                headers=HEADERS,
                params={"session_id": session_id}
            )
            if r.status_code == 200:
                return r.json()
            return {}
    except Exception as e:
        log.error("pinai_persona_failed", error=str(e))
        return {}


# ── MESSAGE PROCESSOR ─────────────────────────────────────────────────────────

async def process_message(user_id: str, content: str) -> str:
    """
    Runs a user message through the full NEXUS-thirdy LangGraph.
    Returns the agent's response string.
    """
    initial_state = {
        "user_id": user_id,
        "platform": "pinai",
        "raw_message": content.strip(),
        "detected_skill": "",
        "requires_payment": False,
        "payment_verified": False,
        "context_pack": "",
        "llm_response": "",
        "final_response": "",
        "messages": []
    }

    try:
        final_state = await nexus_graph.ainvoke(initial_state)
        response = final_state.get("final_response", "")
        return response if response else "I had trouble generating a response. Please try again."
    except Exception as e:
        log.error("pinai_graph_error", user_id=user_id, error=str(e))
        return "I encountered an issue. Please try again in a moment."


# ── POLLING LOOP ──────────────────────────────────────────────────────────────

async def pinai_polling_loop():
    """
    Main polling loop.
    Runs forever as a background asyncio task inside FastAPI.
    Starts automatically on server startup via lifespan.
    """

    if not settings.has_pinai():
        log.warning(
            "pinai_disabled",
            reason="PINAI_API_KEY or PINAI_AGENT_ID not set — skipping PIN AI connection"
        )
        return

    log.info("pinai_polling_started", agent_id=settings.PINAI_AGENT_ID)

    poll_count = 0

    while True:
        try:
            poll_count += 1

            # Poll for new messages
            messages = await poll_messages()

            for msg in messages:
                session_id = msg.get("session_id")
                content = msg.get("content", "").strip()
                persona_id = msg.get("persona_id") or msg.get("id")

                if not session_id or not content:
                    continue

                # Use session_id as user_id for memory scoping
                user_id = session_id

                # Send greeting to new sessions
                if session_id not in _greeted_sessions:
                    _greeted_sessions.add(session_id)
                    greeting = await process_message(user_id, "hello")
                    await reply_message(session_id, persona_id, greeting)
                    log.info("pinai_greeted", session=session_id)
                    continue

                # Process normal message through LangGraph
                response = await process_message(user_id, content)
                sent = await reply_message(session_id, persona_id, response)

                log.info(
                    "pinai_replied",
                    session=session_id,
                    content_preview=content[:50],
                    sent=sent
                )

        except Exception as e:
            log.error("pinai_loop_error", error=str(e))

        await asyncio.sleep(POLL_INTERVAL)

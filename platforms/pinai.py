"""
NEXUS-thirdy | platforms/pinai.py
Phase 5 — PIN AI Connection (Fixed)

Uses the confirmed working AgentHub endpoints:
  POST /api/heartbeat     ← keeps agent online
  GET  /api/messages      ← fetch inbox
  GET  /api/messages/:id  ← fetch messages from a peer
  POST /api/message       ← send a reply

Auth: Authorization: Bearer YOUR_API_KEY
Base URL: https://agents.pinai.tech
"""

import asyncio
import httpx
from config.settings import settings
from agent.graph import nexus_graph
import structlog

log = structlog.get_logger()

HEADERS = {
    "Authorization": f"Bearer {settings.PINAI_API_KEY}",
    "Content-Type": "application/json"
}

POLL_INTERVAL = 5
HEARTBEAT_EVERY = 6

_replied_ids: set = set()
_greeted_ids: set = set()


# ── API CALLS ─────────────────────────────────────────────────────────────────

async def send_heartbeat() -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(
                f"{settings.PINAI_API_URL}/api/heartbeat",
                headers=HEADERS,
                json={"supports_chat": True}
            )
            log.info("pinai_heartbeat", status=r.status_code)
    except Exception as e:
        log.warning("pinai_heartbeat_failed", error=str(e))


async def get_inbox() -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{settings.PINAI_API_URL}/api/messages",
                headers=HEADERS
            )
            if r.status_code == 200:
                return r.json().get("conversations", [])
            log.warning("pinai_inbox_error", status=r.status_code)
            return []
    except Exception as e:
        log.error("pinai_inbox_failed", error=str(e))
        return []


async def get_messages(peer_id: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{settings.PINAI_API_URL}/api/messages/{peer_id}",
                headers=HEADERS
            )
            if r.status_code == 200:
                return r.json().get("messages", [])
            return []
    except Exception as e:
        log.error("pinai_get_messages_failed", peer=peer_id, error=str(e))
        return []


async def send_message(to_agent_id: str, content: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{settings.PINAI_API_URL}/api/message",
                headers=HEADERS,
                json={"to": to_agent_id, "content": content}
            )
            return r.status_code == 200
    except Exception as e:
        log.error("pinai_send_failed", to=to_agent_id, error=str(e))
        return False


# ── MESSAGE PROCESSOR ─────────────────────────────────────────────────────────

async def process_message(user_id: str, content: str) -> str:
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
    if not settings.has_pinai():
        log.warning("pinai_disabled", reason="PINAI_API_KEY or PINAI_AGENT_ID not set")
        return

    log.info("pinai_polling_started", agent_id=settings.PINAI_AGENT_ID)

    # Send heartbeat immediately on startup
    await send_heartbeat()
    poll_count = 0

    while True:
        try:
            poll_count += 1

            # Heartbeat every 30 seconds
            if poll_count % HEARTBEAT_EVERY == 0:
                await send_heartbeat()

            # Fetch inbox
            conversations = await get_inbox()

            for conv in conversations:
                peer = conv.get("peer", {})
                peer_id = peer.get("id")
                unread = conv.get("unread_count", 0)

                if not peer_id or unread == 0:
                    continue

                # Greet new users
                if peer_id not in _greeted_ids:
                    _greeted_ids.add(peer_id)
                    greeting = await process_message(peer_id, "hello")
                    await send_message(peer_id, greeting)
                    log.info("pinai_greeted", peer=peer_id)
                    continue

                # Fetch unread messages
                messages = await get_messages(peer_id)
                for msg in messages:
                    msg_id = msg.get("id")
                    from_id = msg.get("from_agent_id", "")

                    if not msg_id or msg_id in _replied_ids:
                        continue
                    if from_id == settings.PINAI_AGENT_ID:
                        continue

                    content = msg.get("content", "").strip()
                    if not content:
                        continue

                    _replied_ids.add(msg_id)

                    response = await process_message(peer_id, content)
                    sent = await send_message(peer_id, response)

                    log.info(
                        "pinai_replied",
                        peer=peer_id,
                        msg_id=msg_id,
                        sent=sent
                    )

        except Exception as e:
            log.error("pinai_loop_error", error=str(e))

        await asyncio.sleep(POLL_INTERVAL)

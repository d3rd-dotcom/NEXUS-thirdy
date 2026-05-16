"""
NEXUS-thirdy | platforms/pinai.py
Phase 5 Final — PIN AI AgentHub Connection

Agent ID : NEXUS-thirdy-v2-d6321a
Platform : agents.pinai.tech
Auth     : Authorization: Bearer pk_live_...

Correct HTTP polling loop per AgentHub docs:
  POST /api/heartbeat         → stay online, get unread_count
  GET  /api/messages          → list conversations (only if unread_count > 0)
  GET  /api/messages/:peer_id → read thread
  POST /api/messages/:peer_id/read → mark as read
  POST /api/message           → send reply
"""

import asyncio
import httpx
from config.settings import settings
from agent.graph import nexus_graph
import structlog

log = structlog.get_logger()

POLL_INTERVAL = 30       # seconds — docs recommend 30-60s
_replied_ids: set = set()
_greeted_ids: set = set()


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PINAI_API_KEY}",
        "Content-Type": "application/json"
    }


# ── API CALLS ─────────────────────────────────────────────────────────────────

async def send_heartbeat() -> int:
    """
    Keeps NEXUS-thirdy online on AgentHub.
    Returns unread_count from response — use this to decide whether to poll inbox.
    Timeout: agent goes offline after 600 seconds without heartbeat.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{settings.PINAI_API_URL}/api/heartbeat",
                headers=_headers(),
                json={"supports_chat": True}
            )
            if r.status_code == 200:
                data = r.json()
                unread = data.get("unread_count", 0)
                log.info("pinai_heartbeat_ok", unread_count=unread)
                return unread
            log.warning("pinai_heartbeat_error", status=r.status_code)
            return 0
    except Exception as e:
        log.warning("pinai_heartbeat_failed", error=str(e))
        return 0


async def get_inbox() -> list[dict]:
    """Fetch conversations with unread messages."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{settings.PINAI_API_URL}/api/messages",
                headers=_headers(),
                params={"unread_only": "true", "limit": 20}
            )
            if r.status_code == 200:
                data = r.json()
                # Handle both list and dict response formats
                if isinstance(data, list):
                    return data
                return data.get("conversations", data.get("messages", []))
            log.warning("pinai_inbox_error", status=r.status_code)
            return []
    except Exception as e:
        log.error("pinai_inbox_failed", error=str(e))
        return []


async def get_thread(peer_id: str) -> list[dict]:
    """Fetch message history with a specific peer."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{settings.PINAI_API_URL}/api/messages/{peer_id}",
                headers=_headers()
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
                return data.get("messages", [])
            return []
    except Exception as e:
        log.error("pinai_thread_failed", peer=peer_id, error=str(e))
        return []


async def mark_read(peer_id: str, last_msg_id: str) -> None:
    """Advance the read cursor for a conversation."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{settings.PINAI_API_URL}/api/messages/{peer_id}/read",
                headers=_headers(),
                json={"last_read_message_id": last_msg_id}
            )
    except Exception as e:
        log.warning("pinai_mark_read_failed", error=str(e))


async def send_message(to_agent_id: str, content: str) -> bool:
    """Send a reply to a peer."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{settings.PINAI_API_URL}/api/message",
                headers=_headers(),
                json={"to": to_agent_id, "content": content, "metadata": {}}
            )
            return r.status_code == 200
    except Exception as e:
        log.error("pinai_send_failed", to=to_agent_id, error=str(e))
        return False


# ── MESSAGE PROCESSOR ─────────────────────────────────────────────────────────

async def process_message(user_id: str, content: str) -> str:
    """Run a message through the full NEXUS-thirdy LangGraph."""
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
    Main polling loop — runs forever as background asyncio task.
    Follows the official AgentHub HTTP loop pattern:
      heartbeat → check unread_count → poll inbox if needed → reply → mark read
    """
    if not settings.has_pinai():
        log.warning("pinai_disabled", reason="PINAI_API_KEY or PINAI_AGENT_ID not set")
        return

    log.info("pinai_polling_started", agent_id=settings.PINAI_AGENT_ID)

    while True:
        try:
            # Step 1: Heartbeat — also returns unread_count
            unread_count = await send_heartbeat()

            # Step 2: Only poll inbox if there are unread messages
            if unread_count > 0:
                conversations = await get_inbox()

                for conv in conversations:
                    # Handle different response shapes
                    peer_id = (
                        conv.get("peer", {}).get("id") or
                        conv.get("peer_id") or
                        conv.get("id")
                    )
                    if not peer_id:
                        continue

                    # Step 3: Fetch thread
                    messages = await get_thread(peer_id)
                    if not messages:
                        continue

                    last_msg_id = None
                    processed_any = False

                    for msg in messages:
                        msg_id = msg.get("id")
                        from_id = msg.get("from_agent_id", msg.get("sender", ""))
                        content = msg.get("content", "").strip()

                        if not msg_id or not content:
                            continue

                        # Skip own messages and already replied
                        if from_id == settings.PINAI_AGENT_ID:
                            continue
                        if msg_id in _replied_ids:
                            continue

                        _replied_ids.add(msg_id)
                        last_msg_id = msg_id

                        # Greet new users
                        if peer_id not in _greeted_ids:
                            _greeted_ids.add(peer_id)
                            response = await process_message(peer_id, "hello")
                        else:
                            response = await process_message(peer_id, content)

                        sent = await send_message(peer_id, response)
                        processed_any = True

                        log.info(
                            "pinai_replied",
                            peer=peer_id,
                            msg_id=msg_id,
                            sent=sent
                        )

                    # Step 4: Mark read after processing all messages in thread
                    if last_msg_id and processed_any:
                        await mark_read(peer_id, last_msg_id)

        except Exception as e:
            log.error("pinai_loop_error", error=str(e))

        await asyncio.sleep(POLL_INTERVAL)

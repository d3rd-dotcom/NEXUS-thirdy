"""
NEXUS-thirdy | platforms/pinai.py
Phase 5 Final — PIN AI AgentHub Connection

Correct HTTP polling loop per AgentHub docs:
  POST /api/heartbeat              → stay online, get unread_count
  GET  /api/messages               → list conversations (only if unread > 0)
  GET  /api/messages/:peer_id      → read thread
  POST /api/messages/:peer_id/read → mark as read
  POST /api/message                → send reply

FIXED (H1): Replaced unbounded sets with TTL-bounded caches.

Previous implementation used plain Python sets for `_replied_ids` and
`_greeted_ids`. On the Render free tier (512 MB RAM) a busy agent talking to
thousands of unique users would accumulate message IDs indefinitely and
eventually trigger an OOM kill — crashing the entire server with no warning.

cachetools.TTLCache provides:
  - maxsize=10_000 : hard upper bound on entries (LRU eviction when full)
  - ttl=86_400     : entries expire automatically after 24 hours
  - Same `in` / assignment API as dict — minimal code change required

FIXED (C3, C4): process_message() now uses make_initial_state() to build
                the LangGraph initial state. The previous inline dict was
                missing payment_proof, reasoning_trace, reflexion_score,
                reflexion_iteration, and reflexion_critique — causing KeyError
                crashes inside reflexion_node and premium_skills_node for
                any PIN AI message that triggered a premium skill.

FIXED (security): sanitize_user_id() is applied to peer_id before it is
                  used as a Mem0 / Supabase key to prevent key injection.
"""

import asyncio
import httpx
from cachetools import TTLCache  # FIXED (H1): bounded TTL cache
from config.settings import settings
from agent.graph import nexus_graph
from agent.state_factory import make_initial_state  # FIXED (C3, C4)
from security.validators import sanitize_user_id
import structlog

log = structlog.get_logger()

POLL_INTERVAL = 30  # seconds — AgentHub docs recommend 30-60s

# FIXED (H1): TTLCache replaces plain set().
# maxsize=10_000 → LRU eviction prevents unbounded growth
# ttl=86_400     → 24-hour expiry; replied_ids older than a day are irrelevant
_replied_ids: TTLCache = TTLCache(maxsize=10_000, ttl=86_400)
_greeted_ids: TTLCache = TTLCache(maxsize=10_000, ttl=86_400)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PINAI_API_KEY}",
        "Content-Type": "application/json",
    }


# ── API CALLS ─────────────────────────────────────────────────────────────────

async def send_heartbeat() -> int:
    """
    Keeps NEXUS-thirdy online on AgentHub.
    Returns unread_count from the response.
    Agent goes offline after 600 s without a heartbeat.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{settings.PINAI_API_URL}/api/heartbeat",
                headers=_headers(),
                json={"supports_chat": True},
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
                params={"unread_only": "true", "limit": 20},
            )
            if r.status_code == 200:
                data = r.json()
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
                headers=_headers(),
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
                json={"last_read_message_id": last_msg_id},
            )
    except Exception as e:
        log.warning("pinai_mark_read_failed", error=str(e))


async def send_message(to_agent_id: str, content: str) -> bool:
    """Send a reply to a peer agent."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{settings.PINAI_API_URL}/api/message",
                headers=_headers(),
                json={"to": to_agent_id, "content": content, "metadata": {}},
            )
            return r.status_code == 200
    except Exception as e:
        log.error("pinai_send_failed", to=to_agent_id, error=str(e))
        return False


# ── MESSAGE PROCESSOR ─────────────────────────────────────────────────────────

async def process_message(user_id: str, content: str) -> str:
    """
    Run a message through the full NEXUS-thirdy LangGraph.

    FIXED (C3, C4): Uses make_initial_state() — all ThirdyState keys are
    guaranteed to be present. The previous inline dict was missing five keys
    that caused KeyError crashes in reflexion and premium skill nodes.

    FIXED (security): Sanitizes user_id before passing it to the graph where
    it is used as a Mem0 / Supabase key.
    """
    # FIXED (security): sanitize before any DB/memory write
    safe_user_id = sanitize_user_id(user_id)

    # FIXED (C3, C4): Factory guarantees all 15 ThirdyState keys are present
    initial_state = make_initial_state(
        user_id=safe_user_id,
        message=content,
        platform="pinai",
    )

    try:
        final_state = await nexus_graph.ainvoke(initial_state)
        response = final_state.get("final_response", "")
        return response if response else "I had trouble generating a response. Please try again."
    except Exception as e:
        log.error("pinai_graph_error", user_id=safe_user_id, error=str(e))
        return "I encountered an issue. Please try again in a moment."


# ── POLLING LOOP ──────────────────────────────────────────────────────────────

async def pinai_polling_loop():
    """
    Main polling loop — runs forever as a background asyncio task inside
    the FastAPI lifespan context.

    Follows the official AgentHub HTTP polling pattern:
      heartbeat → check unread_count → poll inbox → reply → mark read
    """
    if not settings.has_pinai():
        log.warning("pinai_disabled", reason="PINAI_API_KEY or PINAI_AGENT_ID not set")
        return

    log.info("pinai_polling_started", agent_id=settings.PINAI_AGENT_ID)

    while True:
        try:
            # Step 1: Heartbeat — also tells us whether there is anything to do
            unread_count = await send_heartbeat()

            if unread_count > 0:
                conversations = await get_inbox()

                for conv in conversations:
                    # Handle different response shapes from the AgentHub API
                    peer_id = (
                        conv.get("peer", {}).get("id")
                        or conv.get("peer_id")
                        or conv.get("id")
                    )
                    if not peer_id:
                        continue

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

                        # Skip our own outbound messages
                        if from_id == settings.PINAI_AGENT_ID:
                            continue

                        # FIXED (H1): TTLCache membership test (same API as set)
                        if msg_id in _replied_ids:
                            continue

                        # FIXED (H1): TTLCache assignment (replaces set.add())
                        _replied_ids[msg_id] = True
                        last_msg_id = msg_id

                        # Send a greeting the first time we see a peer
                        if peer_id not in _greeted_ids:
                            # FIXED (H1): TTLCache assignment
                            _greeted_ids[peer_id] = True
                            response = await process_message(peer_id, "hello")
                        else:
                            response = await process_message(peer_id, content)

                        sent = await send_message(peer_id, response)
                        processed_any = True

                        log.info(
                            "pinai_replied",
                            peer=peer_id,
                            msg_id=msg_id,
                            sent=sent,
                        )

                    if last_msg_id and processed_any:
                        await mark_read(peer_id, last_msg_id)

        except Exception as e:
            log.error("pinai_loop_error", error=str(e))

        await asyncio.sleep(POLL_INTERVAL)

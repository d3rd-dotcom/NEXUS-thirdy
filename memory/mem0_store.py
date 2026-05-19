"""
NEXUS-thirdy | memory/mem0_store.py
Phase 4 — Mem0 Vector Memory

Handles user-specific semantic memory:
- Stores facts extracted from conversations
- Recalls relevant memories before each LLM call
- Updates existing facts when user corrects the agent (adaptive)

Backend: Supabase pgvector
Embeddings: Cohere embed-english-v3.0

FIXED (M1): Supabase project ref and pooler host were hardcoded as string
            literals inside _build_mem0_config(). They are now read from
            settings.SUPABASE_PROJECT_REF and settings.SUPABASE_POOLER_HOST,
            which are populated from environment variables. This allows the
            same codebase to target different Supabase projects across
            dev / staging / production without any source changes.

FIXED (M3): mem0.add() and mem0.search() are synchronous blocking I/O calls
            that perform network requests to Supabase. Running them directly
            inside an async function blocks the entire asyncio event loop,
            freezing all concurrent requests (PIN AI polling, webhook handling,
            etc.) until the Supabase round-trip completes. Both are now
            dispatched to the default thread-pool executor via run_in_executor.
"""

import asyncio
from mem0 import Memory
from config.settings import settings
import structlog

log = structlog.get_logger()


# ── CONFIG ────────────────────────────────────────────────────────────────────

def _build_mem0_config() -> dict:
    """
    Build the Mem0 configuration dict from environment variables.

    FIXED (M1): Previously contained literal strings:
        "user": "postgres.wmavdqyjkbbrocnagegd"
        "host": "aws-0-ap-southeast-1.pooler.supabase.com"
    These are now read from SUPABASE_PROJECT_REF and SUPABASE_POOLER_HOST.
    """
    project_ref = settings.SUPABASE_PROJECT_REF
    pooler_host = settings.SUPABASE_POOLER_HOST

    if not project_ref:
        log.warning(
            "mem0_config_missing_project_ref",
            hint="Set SUPABASE_PROJECT_REF in environment variables",
        )

    return {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "collection_name": "nexus_memories",
                "dbname": "postgres",
                # FIXED (M1): Was hardcoded "postgres.wmavdqyjkbbrocnagegd"
                "user": f"postgres.{project_ref}",
                "password": settings.SUPABASE_SERVICE_KEY,
                # FIXED (M1): Was hardcoded "aws-0-ap-southeast-1.pooler.supabase.com"
                "host": pooler_host,
                "port": 5432,
                "embedding_model_dims": 1024,
            },
        },
        "embedder": {
            "provider": "cohere",
            "config": {
                "api_key": settings.COHERE_API_KEY,
                "model": "embed-english-v3.0",
            },
        },
        "llm": {
            "provider": "groq",
            "config": {
                "api_key": settings.GROQ_API_KEY,
                "model": "llama-3.1-8b-instant",
            },
        },
    }


# ── MEMORY CLASS ──────────────────────────────────────────────────────────────

class NexusMemory:
    """
    Wraps Mem0 OSS with NEXUS-thirdy specific methods.
    All blocking I/O is dispatched to the thread-pool executor so it never
    stalls the event loop.
    """

    def __init__(self):
        self._mem = None
        # FIXED (M1): Added SUPABASE_PROJECT_REF to the required-keys check.
        # Without a project ref the pgvector connection string is malformed and
        # Mem0 init fails anyway — failing early with a clear log is better.
        self._enabled = bool(
            settings.SUPABASE_URL
            and settings.COHERE_API_KEY
            and settings.SUPABASE_PROJECT_REF
        )

    def _get_mem(self):
        """Lazy initialisation — only connect when first needed."""
        if self._mem is None and self._enabled:
            try:
                self._mem = Memory.from_config(_build_mem0_config())
                log.info("mem0_initialized")
            except Exception as e:
                log.error("mem0_init_failed", error=str(e))
                self._enabled = False
        return self._mem

    async def remember(self, user_id: str, messages: list[dict]) -> None:
        """
        Extract and store facts from a conversation exchange.
        Called AFTER the response is sent — non-blocking from the caller's
        perspective.

        FIXED (M3): mem0.add() is a synchronous function that makes network
        calls to Supabase. Awaiting it directly in an async function would
        block the event loop for the full round-trip duration (~100-500 ms),
        serialising all concurrent agent requests. It is now dispatched to
        the default ThreadPoolExecutor via run_in_executor.

        messages format:
            [
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."}
            ]
        """
        mem = self._get_mem()
        if not mem:
            return
        try:
            loop = asyncio.get_event_loop()
            # FIXED (M3): Offload blocking Supabase I/O to the thread pool
            await loop.run_in_executor(
                None,
                lambda: mem.add(messages, user_id=user_id),
            )
            log.info("memory_stored", user_id=user_id)
        except Exception as e:
            log.error("memory_store_failed", user_id=user_id, error=str(e))
            # Memory failure is non-fatal — agent keeps running

    async def recall(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        """
        Retrieve relevant memories for a user + query pair.
        Returns empty list if memory is unavailable — agent degrades gracefully.

        FIXED (M3): mem0.search() is also synchronous blocking I/O.
        Dispatched to the executor for the same reasons as remember().
        """
        mem = self._get_mem()
        if not mem:
            return []
        try:
            loop = asyncio.get_event_loop()
            # FIXED (M3): Offload blocking Supabase I/O to the thread pool
            results = await loop.run_in_executor(
                None,
                lambda: mem.search(query=query, user_id=user_id, limit=limit),
            )
            return results.get("results", [])
        except Exception as e:
            log.error("memory_recall_failed", user_id=user_id, error=str(e))
            return []

    async def update_fact(self, user_id: str, old_fact: str, new_fact: str) -> None:
        """
        Update an existing memory fact rather than storing a duplicate.
        Used when the user corrects a previously stored belief.

        FIXED (M3): Both the search and update calls are blocking I/O;
        dispatched to the executor.
        """
        mem = self._get_mem()
        if not mem:
            return
        try:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: mem.search(query=old_fact, user_id=user_id, limit=1),
            )
            if results.get("results"):
                mem_id = results["results"][0]["id"]
                await loop.run_in_executor(
                    None,
                    lambda: mem.update(mem_id, new_fact),
                )
                log.info("memory_updated", user_id=user_id, mem_id=mem_id)
        except Exception as e:
            log.error("memory_update_failed", error=str(e))

    @property
    def is_enabled(self) -> bool:
        return self._enabled


# Singleton — imported by context_builder and memory_update_node
nexus_memory = NexusMemory()

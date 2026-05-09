"""
NEXUS-thirdy | memory/mem0_store.py
Phase 4 — Mem0 Vector Memory

Handles user-specific memory:
- Stores facts extracted from conversations
- Recalls relevant memories before each LLM call
- Updates existing facts when user corrects the agent (adaptive)

Backend: Supabase pgvector (free, same database used everywhere)
Embeddings: Cohere embed-english-v3.0 (you already have the API key)
"""

from mem0 import Memory
from config.settings import settings
import structlog

log = structlog.get_logger()

# ── CONFIG ────────────────────────────────────────────────────────────────────

def _build_mem0_config() -> dict:
    return {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "collection_name": "nexus_memories",
                "dbname": "postgres",
                "user": "postgres.wmavdqyjkbbrocnagegd",
                "password": settings.SUPABASE_SERVICE_KEY,
                "host": "aws-0-ap-southeast-1.pooler.supabase.com",  # Replace with your Supabase host
                "port": 5432,
                "embedding_model_dims": 1024
            }
        },
        "embedder": {
            "provider": "cohere",
            "config": {
                "api_key": settings.COHERE_API_KEY,
                "model": "embed-english-v3.0"
            }
        },
        "llm": {
            "provider": "groq",
            "config": {
                "api_key": settings.GROQ_API_KEY,
                "model": "llama-3.1-8b-instant"
            }
        }
    }


# ── MEMORY CLASS ──────────────────────────────────────────────────────────────

class NexusMemory:
    """
    Wraps Mem0 OSS with NEXUS-thirdy specific methods.
    Memory writes happen asynchronously — users never wait for storage.
    """

    def __init__(self):
        self._mem = None
        self._enabled = bool(settings.SUPABASE_URL and settings.COHERE_API_KEY)

    def _get_mem(self):
        """Lazy initialization — only connect when first needed."""
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
        Called AFTER the response is sent — non-blocking.

        messages format:
        [
            {"role": "user", "content": "I prefer low-risk DeFi protocols"},
            {"role": "assistant", "content": "Got it, I'll focus on low-risk options."}
        ]
        """
        mem = self._get_mem()
        if not mem:
            return

        try:
            mem.add(messages, user_id=user_id)
            log.info("memory_stored", user_id=user_id)
        except Exception as e:
            log.error("memory_store_failed", user_id=user_id, error=str(e))
            # Memory failure is non-fatal — agent keeps running

    async def recall(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        """
        Retrieve relevant memories for a user + query pair.
        Returns list of memory dicts with 'memory' and 'score' fields.
        Returns empty list if memory is unavailable — agent degrades gracefully.
        """
        mem = self._get_mem()
        if not mem:
            return []

        try:
            results = mem.search(query=query, user_id=user_id, limit=limit)
            return results.get("results", [])
        except Exception as e:
            log.error("memory_recall_failed", user_id=user_id, error=str(e))
            return []

    async def update_fact(self, user_id: str, old_fact: str, new_fact: str) -> None:
        """
        When a user corrects the agent, update the existing fact
        instead of storing a duplicate.
        """
        mem = self._get_mem()
        if not mem:
            return

        try:
            results = mem.search(query=old_fact, user_id=user_id, limit=1)
            if results.get("results"):
                mem_id = results["results"][0]["id"]
                mem.update(mem_id, new_fact)
                log.info("memory_updated", user_id=user_id, mem_id=mem_id)
        except Exception as e:
            log.error("memory_update_failed", error=str(e))

    @property
    def is_enabled(self) -> bool:
        return self._enabled


# Singleton — imported by context_builder and memory_update_node
nexus_memory = NexusMemory()

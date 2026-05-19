"""
NEXUS-thirdy | memory/graphiti_store.py
Phase 4 — Graphiti Temporal Knowledge Graph

Handles entity-relationship memory with temporal reasoning:
- Tracks WHAT is true AND WHEN it became true
- Stores successful skill traces as reusable procedures
- Multi-hop relationship queries

This works alongside Mem0:
  Mem0     = "what does this user prefer?" (semantic similarity)
  Graphiti = "what was true last week vs now?" (temporal reasoning)

Backend: Neo4j Aura Free

FIXED (M6): Connection reset on operation failure so the next call retries
            automatically. Previously a single failed init permanently set
            `self._enabled = False`, disabling Graphiti for the entire server
            lifetime — including after transient network blips where Neo4j
            would have recovered in seconds. The fix separates two distinct
            concepts:

              _has_credentials  → True if env vars are configured (never changes)
              _g                → The live Graphiti connection (can be None after
                                  a failure and will be retried on next call)

            Any operation that raises an exception calls _reset_connection()
            which sets self._g = None. The next operation will re-enter
            _get_graph() and attempt a fresh connection.
"""

import json
import asyncio
from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType
from config.settings import settings
import structlog
from datetime import datetime, timezone

log = structlog.get_logger()


class NexusGraph:
    """
    Wraps Graphiti OSS with NEXUS-thirdy specific methods.
    Gracefully disabled when Neo4j credentials are not configured.
    Automatically reconnects after transient failures.
    """

    def __init__(self):
        self._g = None

        # FIXED (M6): Track credential availability separately from connection
        # state. _has_credentials is set once at startup and never changes.
        # _g is the live connection object — it can become None after a failure
        # and is re-created on the next _get_graph() call.
        self._has_credentials = bool(
            settings.GRAPHITI_NEO4J_URI
            and settings.GRAPHITI_NEO4J_USER
            and settings.GRAPHITI_NEO4J_PASSWORD
        )

    @property
    def _enabled(self) -> bool:
        """
        True as long as credentials are configured.

        FIXED (M6): Previously this was a mutable instance attribute set to
        False on first failure, permanently disabling all Graphiti operations.
        Now it reflects credential availability only — the connection itself
        is managed separately via _g and _reset_connection().
        """
        return self._has_credentials

    def _get_graph(self):
        """
        Return the live Graphiti connection, creating it if needed.

        FIXED (M6): If self._g is None but credentials exist (either first
        call or after a reset), we always attempt to reconnect. Previously
        a single Exception here permanently set self._enabled = False and
        short-circuited all future calls.
        """
        if not self._has_credentials:
            return None

        if self._g is None:
            try:
                self._g = Graphiti(
                    uri=settings.GRAPHITI_NEO4J_URI,
                    user=settings.GRAPHITI_NEO4J_USER,
                    password=settings.GRAPHITI_NEO4J_PASSWORD,
                )
                log.info("graphiti_connected")
            except Exception as e:
                log.error("graphiti_connect_failed", error=str(e))
                # FIXED (M6): Leave self._g as None so the next call retries.
                # Do NOT set self._has_credentials = False here.
                self._g = None

        return self._g

    def _reset_connection(self) -> None:
        """
        Mark the current connection as dead so the next call triggers a
        fresh connection attempt.

        FIXED (M6): This method is called in every except block so that
        transient Neo4j errors (network timeout, auth token expiry, etc.)
        are recovered from automatically on the next request rather than
        permanently disabling memory for the server lifetime.
        """
        log.warning("graphiti_connection_reset_will_retry_on_next_call")
        self._g = None

    # ── PUBLIC API ────────────────────────────────────────────────────────────

    async def store_episode(self, user_id: str, content: str) -> None:
        """
        Store a conversation turn as a Graphiti episode.
        Graphiti automatically extracts entities and relationships.
        """
        g = self._get_graph()
        if not g:
            return
        try:
            await g.add_episode(
                name=f"{user_id}_{datetime.now(timezone.utc).isoformat()}",
                episode_body=content,
                source_description=f"nexus_thirdy conversation with {user_id}",
                reference_time=datetime.now(timezone.utc),
                source=EpisodeType.message,
            )
        except Exception as e:
            log.error("graphiti_episode_failed", error=str(e))
            # FIXED (M6): Reset so the next store_episode call reconnects
            self._reset_connection()

    async def search_graph(self, query: str) -> list[dict]:
        """
        Semantic + temporal search of the knowledge graph.
        Returns current facts only (filters out invalidated entries).
        Returns empty list on any error — callers degrade gracefully.
        """
        g = self._get_graph()
        if not g:
            return []
        try:
            results = await g.search(query)
            return [
                {
                    "fact": r.fact,
                    "valid_from": str(r.valid_at) if hasattr(r, "valid_at") else None,
                    "score": r.score if hasattr(r, "score") else 1.0,
                }
                for r in results
                # Filter out facts that have been superseded / invalidated
                if not (hasattr(r, "invalid_at") and r.invalid_at)
            ]
        except Exception as e:
            log.error("graphiti_search_failed", error=str(e))
            # FIXED (M6): Reset connection for retry on next request
            self._reset_connection()
            return []

    async def archive_procedure(
        self,
        skill_name: str,
        inputs: dict,
        reasoning_trace: str,
        final_output: str,
        reflexion_score: float,
    ) -> None:
        """
        Archive a high-scoring skill trace as a reusable procedure.
        Called by the Reflexion node when score >= 8.0.
        """
        g = self._get_graph()
        if not g:
            return

        procedure_body = (
            f"PROCEDURE: {skill_name} | SCORE: {reflexion_score}/10\n"
            f"INPUTS: {json.dumps(inputs)}\n"
            f"REASONING: {reasoning_trace}\n"
            f"OUTPUT: {final_output}"
        )
        try:
            await g.add_episode(
                name=f"procedure_{skill_name}_{datetime.now(timezone.utc).isoformat()}",
                episode_body=procedure_body,
                source_description=f"archived procedure for {skill_name}",
                reference_time=datetime.now(timezone.utc),
                source=EpisodeType.text,
            )
            log.info("procedure_archived", skill=skill_name, score=reflexion_score)
        except Exception as e:
            log.error("procedure_archive_failed", error=str(e))
            # FIXED (M6): Reset connection for retry on next request
            self._reset_connection()

    async def search_procedures(self, task_description: str) -> list[dict]:
        """
        Check whether a validated procedure already exists for a similar task.
        Called before running a premium skill from scratch.
        """
        results = await self.search_graph(f"PROCEDURE {task_description}")
        return [r for r in results if "PROCEDURE:" in r.get("fact", "")]

    @property
    def is_enabled(self) -> bool:
        return self._enabled


# Singleton — imported by context_builder, reflexion, and memory_update_node
nexus_graph_store = NexusGraph()

"""
NEXUS-thirdy | memory/graphiti_store.py
Phase 4 — Graphiti Temporal Knowledge Graph

Handles entity-relationship memory with temporal reasoning:
- Tracks WHAT is true AND WHEN it became true
- Stores successful skill traces as reusable procedures
- Multi-hop relationship queries ("what DeFi protocols has this user liked?")

This works alongside Mem0:
- Mem0 = "what does this user prefer?" (semantic similarity)
- Graphiti = "what was true last week vs now?" (temporal reasoning)

Backend: Neo4j Aura Free (1 free instance, never expires)
Get yours at: https://neo4j.com/cloud/platform/aura-graph-database/
"""

from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType
from config.settings import settings
import structlog
import json
from datetime import datetime, timezone

log = structlog.get_logger()


class NexusGraph:
    """
    Wraps Graphiti OSS with NEXUS-thirdy specific methods.
    Gracefully disabled if Neo4j credentials are not set.
    """

    def __init__(self):
        self._g = None
        self._enabled = bool(
            settings.GRAPHITI_NEO4J_URI and
            settings.GRAPHITI_NEO4J_USER and
            settings.GRAPHITI_NEO4J_PASSWORD
        )

    def _get_graph(self):
        """Lazy initialization — only connect when first needed."""
        if self._g is None and self._enabled:
            try:
                self._g = Graphiti(
                    uri=settings.GRAPHITI_NEO4J_URI,
                    user=settings.GRAPHITI_NEO4J_USER,
                    password=settings.GRAPHITI_NEO4J_PASSWORD
                )
                log.info("graphiti_initialized")
            except Exception as e:
                log.error("graphiti_init_failed", error=str(e))
                self._enabled = False
        return self._g

    async def store_episode(self, user_id: str, content: str) -> None:
        """
        Store a conversation turn as an episode.
        Graphiti automatically extracts entities and relationships.
        Example: "User prefers SOL over ETH" → creates entity nodes + relationship.
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
                source=EpisodeType.message
            )
        except Exception as e:
            log.error("graphiti_episode_failed", error=str(e))

    async def search_graph(self, query: str) -> list[dict]:
        """
        Semantic + temporal search of the knowledge graph.
        Returns current facts (filters out invalidated/outdated ones).
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
                    "score": r.score if hasattr(r, "score") else 1.0
                }
                for r in results
                if not (hasattr(r, "invalid_at") and r.invalid_at)  # Only current facts
            ]
        except Exception as e:
            log.error("graphiti_search_failed", error=str(e))
            return []

    async def archive_procedure(
        self,
        skill_name: str,
        inputs: dict,
        reasoning_trace: str,
        final_output: str,
        reflexion_score: float
    ) -> None:
        """
        Archive a successful skill trace as a reusable procedure.
        Called by Reflexion node when score >= 8.0.
        Future similar tasks can retrieve and replay this procedure.
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
                source=EpisodeType.text
            )
            log.info("procedure_archived", skill=skill_name, score=reflexion_score)
        except Exception as e:
            log.error("procedure_archive_failed", error=str(e))

    async def search_procedures(self, task_description: str) -> list[dict]:
        """
        Before running a premium skill from scratch, check if a validated
        procedure already exists for a similar task.
        Returns matching procedures sorted by score.
        """
        results = await self.search_graph(f"PROCEDURE {task_description}")
        return [r for r in results if "PROCEDURE:" in r.get("fact", "")]

    @property
    def is_enabled(self) -> bool:
        return self._enabled


# Singleton
nexus_graph_store = NexusGraph()

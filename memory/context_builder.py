"""
NEXUS-thirdy | memory/context_builder.py
Phase 4 — Context Builder

Assembles the full context pack before every LLM call.
This is what makes NEXUS-thirdy feel like it knows the user.

Pulls from:
1. Mem0 (vector) — semantic similarity: "what's relevant to this query?"
2. Graphiti (graph) — temporal facts: "what is currently true about this user?"

Both run in parallel. User never waits for one to finish before the other starts.
Result is merged and injected into the supervisor's system prompt.
"""

import asyncio
from memory.mem0_store import nexus_memory
from memory.graphiti_store import nexus_graph_store
import structlog

log = structlog.get_logger()

CONTEXT_TEMPLATE = """## What I know about you
{user_memories}

## Current facts
{graph_facts}
"""

NO_MEMORY_TEXT = "First interaction — no prior memory yet."
NO_GRAPH_TEXT = "No entity relationships recorded yet."


async def build_context_pack(user_id: str, query: str) -> str:
    """
    Runs Mem0 recall and Graphiti search in parallel.
    Returns a formatted string injected into every LLM call.
    Called by the supervisor node before routing.
    """

    # Run both retrievals concurrently — faster than sequential
    mem_results, graph_results = await asyncio.gather(
        nexus_memory.recall(user_id=user_id, query=query, limit=5),
        nexus_graph_store.search_graph(query=query),
        return_exceptions=True  # Don't crash if one fails
    )

    # Handle exceptions from gather
    if isinstance(mem_results, Exception):
        log.error("mem0_gather_error", error=str(mem_results))
        mem_results = []

    if isinstance(graph_results, Exception):
        log.error("graphiti_gather_error", error=str(graph_results))
        graph_results = []

    # Format Mem0 results
    if mem_results:
        mem_lines = "\n".join(
            f"- {r['memory']}"
            for r in mem_results
            if isinstance(r, dict) and r.get("memory")
        )
        mem_text = mem_lines if mem_lines else NO_MEMORY_TEXT
    else:
        mem_text = NO_MEMORY_TEXT

    # Format Graphiti results (current facts only)
    if graph_results:
        graph_lines = "\n".join(
            f"- {r['fact']}"
            for r in graph_results[:5]
            if isinstance(r, dict) and r.get("fact")
        )
        graph_text = graph_lines if graph_lines else NO_GRAPH_TEXT
    else:
        graph_text = NO_GRAPH_TEXT

    # If both are empty, return minimal context
    if mem_text == NO_MEMORY_TEXT and graph_text == NO_GRAPH_TEXT:
        return ""

    return CONTEXT_TEMPLATE.format(
        user_memories=mem_text,
        graph_facts=graph_text
    )


async def save_conversation(
    user_id: str,
    user_message: str,
    agent_response: str
) -> None:
    """
    Saves a conversation turn to both memory stores.
    Called AFTER the response is sent — fire and forget.
    Never blocks the user's response.
    """
    messages = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": agent_response}
    ]

    # Fire both saves concurrently, don't await
    asyncio.create_task(
        nexus_memory.remember(user_id=user_id, messages=messages)
    )
    asyncio.create_task(
        nexus_graph_store.store_episode(user_id=user_id, content=user_message)
    )

    log.info("conversation_save_queued", user_id=user_id)

"""
NEXUS-thirdy | api/server.py
Phase 1 — Server Skeleton

Only three endpoints for now:
  GET  /health     ← Koyeb uses this to know the server is alive
  GET  /status     ← Basic agent info
  GET  /skill.md   ← Skill manifest (hardcoded for now, dynamic in Phase 2)

No agent logic yet. No LLM calls. Just a running server.
"""

import time
import os
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

# ── APP ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="NEXUS-thirdy",
    description="Server-native AI agent. No laptop required.",
    version="0.1.0"
)

START_TIME = time.time()

# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    """
    Koyeb pings this every 30 seconds.
    If it returns anything other than 200, Koyeb restarts the container.
    Keep this endpoint fast and dependency-free.
    """
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME)
    }


@app.get("/status")
async def status():
    """
    Human-readable agent status.
    Will include live skill count and memory stats in later phases.
    """
    return {
        "agent": "NEXUS-thirdy",
        "version": "0.1.0",
        "phase": "1 - server skeleton",
        "environment": os.environ.get("ENVIRONMENT", "development"),
        "uptime_seconds": int(time.time() - START_TIME),
        "skills": "loading in phase 2",
        "memory": "loading in phase 4",
    }


@app.get("/skill.md", response_class=PlainTextResponse)
async def skill_manifest():
    """
    Skill manifest endpoint — required by PIN AI AgentHub.
    Hardcoded for Phase 1. Will be auto-generated from skill registry in Phase 2.
    """
    return """# NEXUS-thirdy

An intelligent AI agent with hybrid memory, deep crypto intelligence, and autonomous payments.

## Status
Currently deploying. Skills coming online soon.

## Agent
- Built by: Leonardo Amora III (@thirdy12356)
- Platform: Koyeb (always-on, no laptop required)
"""

"""
NEXUS-thirdy | api/server.py
Phase 2 — Updated Server

Changes from Phase 1:
  - /skill.md now auto-generated from skill registry (no more hardcoded text)
  - /status now shows real skill counts from registry
  - Settings imported from config/settings.py
"""

import time
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from config.settings import settings
from config.skill_registry import generate_skill_manifest, skill_count

# ── APP ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="NEXUS-thirdy",
    description="Server-native AI agent. No laptop required.",
    version="0.2.0"
)

START_TIME = time.time()

# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME)
    }


@app.get("/status")
async def status():
    counts = skill_count()
    return {
        "agent": "NEXUS-thirdy",
        "version": "0.2.0",
        "phase": "2 - configuration and skill registry",
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": int(time.time() - START_TIME),
        "skills": counts,
        "llm_ready": settings.has_groq(),
        "pinai_ready": settings.has_pinai(),
        "memory": "loading in phase 4",
    }


@app.get("/skill.md", response_class=PlainTextResponse)
async def skill_manifest():
    return generate_skill_manifest()

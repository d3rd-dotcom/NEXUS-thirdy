"""
NEXUS-thirdy | api/server.py
Phase 5 — Updated Server with PIN AI Background Task

Changes from Phase 3:
  - Added lifespan context manager (replaces @app.on_event which is deprecated)
  - PIN AI polling loop starts automatically on server startup
  - PIN AI polling loop stops cleanly on server shutdown
  - No separate process, no CMD window, no manual startup
"""

import time
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from config.settings import settings
from config.skill_registry import generate_skill_manifest, skill_count
from agent.graph import nexus_graph
from platforms.pinai import pinai_polling_loop
import structlog

log = structlog.get_logger()

START_TIME = time.time()


# ── LIFESPAN ──────────────────────────────────────────────────────────────────
# Runs on startup and shutdown.
# This is where background tasks are launched.

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ──
    log.info("nexus_starting", environment=settings.ENVIRONMENT)

    # Launch PIN AI polling as background task
    # It runs forever alongside the FastAPI server — no separate process needed
    pinai_task = asyncio.create_task(pinai_polling_loop())
    log.info("pinai_task_launched")

    yield  # Server is running

    # ── SHUTDOWN ──
    pinai_task.cancel()
    try:
        await pinai_task
    except asyncio.CancelledError:
        log.info("pinai_task_stopped")


# ── APP ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="NEXUS-thirdy",
    description="Server-native AI agent. No laptop required.",
    version="0.5.0",
    lifespan=lifespan
)


# ── MODELS ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    user_id: str
    message: str
    platform: str = "webhook"
    payment_proof: str | None = None


class ChatResponse(BaseModel):
    response: str
    skill_used: str
    cost_usdc: float


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
        "version": "0.5.0",
        "phase": "5 - PIN AI connected",
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": int(time.time() - START_TIME),
        "skills": counts,
        "llm_ready": settings.has_groq(),
        "pinai_ready": settings.has_pinai(),
        "memory_ready": {
            "vector": bool(settings.SUPABASE_URL),
            "graph": bool(settings.GRAPHITI_NEO4J_URI)
        }
    }


@app.get("/skill.md", response_class=PlainTextResponse)
async def skill_manifest():
    return generate_skill_manifest()


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if len(req.message) > 2000:
        raise HTTPException(status_code=400, detail="Message too long (max 2000 characters)")

    initial_state = {
        "user_id": req.user_id,
        "platform": req.platform,
        "raw_message": req.message.strip(),
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
    except Exception as e:
        log.error("graph_error", user_id=req.user_id, error=str(e))
        raise HTTPException(status_code=500, detail="Agent processing error")

    skill_used = final_state.get("detected_skill", "unknown")

    from config.skill_registry import get_skill
    skill_def = get_skill(skill_used)
    cost = skill_def.price_usdc if skill_def and final_state.get("payment_verified") else 0.0

    return ChatResponse(
        response=final_state.get("final_response", ""),
        skill_used=skill_used,
        cost_usdc=cost
    )


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    platform = request.headers.get("X-Platform", "webhook")

    if platform == "pinai":
        user_id = body.get("from_agent_id", "unknown")
        message = body.get("content", "")
    else:
        user_id = body.get("user_id", body.get("from", body.get("sender", "unknown")))
        message = body.get("message", body.get("content", body.get("text", "")))

    if not message:
        return {"status": "empty_message"}

    req = ChatRequest(user_id=user_id, message=message, platform=platform)
    return await chat(req)

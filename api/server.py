"""
NEXUS-thirdy | api/server.py
Phase 3 — Updated Server

Changes from Phase 2:
  - Added POST /chat endpoint (NEXUS-thirdy now talks)
  - Added POST /webhook endpoint (receives messages from platforms)
  - Agent graph imported and invoked on every message
"""

import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from config.settings import settings
from config.skill_registry import generate_skill_manifest, skill_count
from agent.graph import nexus_graph
import structlog

log = structlog.get_logger()

app = FastAPI(
    title="NEXUS-thirdy",
    description="Server-native AI agent. No laptop required.",
    version="0.3.0"
)

START_TIME = time.time()


# ── REQUEST / RESPONSE MODELS ─────────────────────────────────────────────────

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
        "version": "0.3.0",
        "phase": "3 - LangGraph brain active",
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


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    Main chat endpoint. Receives a message, runs it through the LangGraph,
    returns the agent's response.
    """

    # Basic input validation
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if len(req.message) > 2000:
        raise HTTPException(status_code=400, detail="Message too long (max 2000 characters)")

    # Build initial state
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

    # Run through the LangGraph
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
async def webhook(request: dict):
    """
    Generic webhook endpoint.
    Normalizes messages from different platforms into ChatRequest format.
    """
    # Detect platform and extract message
    user_id = request.get("user_id", request.get("from", request.get("sender", "unknown")))
    message = request.get("message", request.get("content", request.get("text", "")))
    platform = request.get("platform", "webhook")

    if not message:
        return {"status": "empty_message"}

    req = ChatRequest(user_id=user_id, message=message, platform=platform)
    return await chat(req)

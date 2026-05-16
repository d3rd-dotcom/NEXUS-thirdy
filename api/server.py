"""
NEXUS-thirdy | api/server.py
Phase 7 — Final Server

Changes from Phase 5:
  - Added POST /agent endpoint (AgentHub skill calls)
  - Added x402 payment verification on premium skills
  - Added GET /wallet endpoint (show agent wallet info)
  - Cerebras fallback in supervisor for Groq rate limits
"""

import time
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel

from config.settings import settings
from config.skill_registry import generate_skill_manifest, skill_count, get_skill
from agent.graph import nexus_graph
from platforms.pinai import pinai_polling_loop
from payments.x402_middleware import verify_payment, build_payment_required_response
from payments.wallet import get_wallet_address, get_balance
import structlog

log = structlog.get_logger()
START_TIME = time.time()


# ── LIFESPAN ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("nexus_starting", environment=settings.ENVIRONMENT)
    pinai_task = asyncio.create_task(pinai_polling_loop())
    log.info("pinai_task_launched")
    yield
    pinai_task.cancel()
    try:
        await pinai_task
    except asyncio.CancelledError:
        log.info("pinai_task_stopped")


# ── APP ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="NEXUS-thirdy",
    description="Server-native AI agent. No laptop required.",
    version="0.7.0",
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


class AgentSkillRequest(BaseModel):
    """AgentHub skill call format."""
    skill: str
    parameters: dict = {}
    request_id: str = ""


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok", "uptime_seconds": int(time.time() - START_TIME)}


@app.get("/status")
async def status():
    wallet_address = await get_wallet_address()
    counts = skill_count()
    return {
        "agent": "NEXUS-thirdy",
        "version": "0.7.0",
        "phase": "7 - premium skills, reflexion, x402 payments",
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": int(time.time() - START_TIME),
        "skills": counts,
        "llm_ready": settings.has_groq(),
        "pinai_ready": settings.has_pinai(),
        "payments_ready": bool(settings.AGENT_WALLET_ADDRESS),
        "wallet": wallet_address or "not configured",
        "memory_ready": {
            "vector": bool(settings.SUPABASE_URL),
            "graph": bool(settings.GRAPHITI_NEO4J_URI)
        }
    }


@app.get("/skill.md", response_class=PlainTextResponse)
async def skill_manifest():
    return generate_skill_manifest()


@app.get("/wallet")
async def wallet_info():
    """Show NEXUS-thirdy's wallet address and balance."""
    address = await get_wallet_address()
    balance = await get_balance()
    return {
        "address": address,
        "balance": balance,
        "network": settings.X402_NETWORK
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Main chat endpoint."""
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
        "payment_proof": req.payment_proof or "",
        "context_pack": "",
        "llm_response": "",
        "reasoning_trace": "",
        "reflexion_score": 0.0,
        "reflexion_iteration": 0,
        "reflexion_critique": "",
        "final_response": "",
        "messages": []
    }

    try:
        final_state = await nexus_graph.ainvoke(initial_state)
    except Exception as e:
        log.error("graph_error", user_id=req.user_id, error=str(e))
        raise HTTPException(status_code=500, detail="Agent processing error")

    skill_used = final_state.get("detected_skill", "unknown")
    skill_def = get_skill(skill_used)
    cost = skill_def.price_usdc if skill_def and final_state.get("payment_verified") else 0.0

    return ChatResponse(
        response=final_state.get("final_response", ""),
        skill_used=skill_used,
        cost_usdc=cost
    )


@app.post("/agent")
async def agent_skill_call(req: AgentSkillRequest, request: Request):
    """
    AgentHub skill call endpoint.
    AgentHub POSTs here when another agent or user calls a NEXUS-thirdy skill.

    Request format (from AgentHub):
      {"skill": "chat", "parameters": {"message": "..."}, "request_id": "req_xxx"}

    Response format (expected by AgentHub):
      {"result": "...", "data": {}}
    """
    skill_id = req.skill
    parameters = req.parameters
    request_id = req.request_id

    log.info("agent_skill_called", skill=skill_id, request_id=request_id)

    # Extract message from parameters
    message = parameters.get("message", parameters.get("query", parameters.get("text", "")))
    user_id = parameters.get("user_id", f"agenthub_{request_id}")

    if not message:
        return {"result": "No message provided.", "data": {}}

    # Check x402 payment for premium skills
    skill_def = get_skill(skill_id)
    if skill_def and skill_def.requires_payment:
        payment_proof = request.headers.get("X-Payment", "")
        wallet = await get_wallet_address()

        is_paid, reason = await verify_payment(
            skill_id=skill_id,
            payment_proof=payment_proof,
            user_address=user_id
        )

        if not is_paid:
            payment_info = build_payment_required_response(skill_id, wallet)
            return JSONResponse(
                status_code=402,
                content=payment_info
            )

    # Process through LangGraph
    initial_state = {
        "user_id": user_id,
        "platform": "agenthub",
        "raw_message": message,
        "detected_skill": skill_id if skill_def else "",
        "requires_payment": bool(skill_def and skill_def.requires_payment),
        "payment_verified": True if (skill_def and not skill_def.requires_payment) else False,
        "payment_proof": request.headers.get("X-Payment", ""),
        "context_pack": "",
        "llm_response": "",
        "reasoning_trace": "",
        "reflexion_score": 0.0,
        "reflexion_iteration": 0,
        "reflexion_critique": "",
        "final_response": "",
        "messages": []
    }

    try:
        final_state = await nexus_graph.ainvoke(initial_state)
        result = final_state.get("final_response", "")
        return {
            "result": result,
            "data": {
                "skill_used": final_state.get("detected_skill", skill_id),
                "request_id": request_id,
                "reflexion_score": final_state.get("reflexion_score")
            }
        }
    except Exception as e:
        log.error("agent_skill_error", skill=skill_id, error=str(e))
        return {"result": "Error processing skill request.", "data": {"error": str(e)}}


@app.post("/webhook")
async def webhook(request: Request):
    """Generic webhook for all platforms."""
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

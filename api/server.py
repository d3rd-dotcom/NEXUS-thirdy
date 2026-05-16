"""
NEXUS-thirdy | api/server.py
Phase 8 — Security Layer Integrated

Changes from Phase 7:
  - Input validation on /chat and /webhook endpoints
  - Output validation before every response
  - Security scan integrated via supervisor (not duplicated here)
  - Behavioral drift logging to Supabase
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
from security.validators import validate_input, validate_output, sanitize_user_id
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
    version="0.8.0",
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
    skill: str
    parameters: dict = {}
    request_id: str = ""


# ── DRIFT LOGGING ─────────────────────────────────────────────────────────────

async def log_interaction(user_id: str, skill: str, platform: str, success: bool):
    """
    Log every interaction to Supabase for behavioral drift monitoring.
    Non-blocking — fire and forget.
    Drift analysis runs weekly as a separate script (Phase 10).
    """
    if not settings.SUPABASE_URL:
        return

    try:
        from supabase import create_client
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
        supabase.table("nexus_interactions").insert({
            "user_id": user_id,
            "skill": skill,
            "platform": platform,
            "success": success,
            "timestamp": time.time()
        }).execute()
    except Exception as e:
        log.error("drift_log_failed", error=str(e))


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
        "version": "0.8.0",
        "phase": "8 - security layer active",
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": int(time.time() - START_TIME),
        "skills": counts,
        "llm_ready": settings.has_groq(),
        "pinai_ready": settings.has_pinai(),
        "payments_ready": bool(settings.AGENT_WALLET_ADDRESS),
        "security": {
            "firewall": settings.LLAMAFIREWALL_ENABLED,
            "input_validation": True,
            "output_validation": True,
            "drift_logging": bool(settings.SUPABASE_URL)
        },
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
    address = await get_wallet_address()
    balance = await get_balance()
    return {"address": address, "balance": balance, "network": settings.X402_NETWORK}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Main chat endpoint with full security layer."""

    # Sanitize user_id
    user_id = sanitize_user_id(req.user_id)

    # Input validation
    validation = validate_input(req.message, user_id=user_id)
    if not validation.is_valid:
        raise HTTPException(status_code=400, detail=validation.reason)

    initial_state = {
        "user_id": user_id,
        "platform": req.platform,
        "raw_message": validation.sanitized or req.message.strip(),
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
        log.error("graph_error", user_id=user_id, error=str(e))
        asyncio.create_task(log_interaction(user_id, "error", req.platform, False))
        raise HTTPException(status_code=500, detail="Agent processing error")

    skill_used = final_state.get("detected_skill", "unknown")
    raw_response = final_state.get("final_response", "")

    # Output validation
    output_validation = validate_output(raw_response, skill_id=skill_used)
    final_response = output_validation.sanitized or raw_response

    # Log interaction for drift monitoring
    asyncio.create_task(
        log_interaction(user_id, skill_used, req.platform, True)
    )

    skill_def = get_skill(skill_used)
    cost = skill_def.price_usdc if skill_def and final_state.get("payment_verified") else 0.0

    return ChatResponse(
        response=final_response,
        skill_used=skill_used,
        cost_usdc=cost
    )


@app.post("/agent")
async def agent_skill_call(req: AgentSkillRequest, request: Request):
    """AgentHub skill call endpoint."""
    skill_id = req.skill
    parameters = req.parameters
    request_id = req.request_id

    log.info("agent_skill_called", skill=skill_id, request_id=request_id)

    message = parameters.get("message", parameters.get("query", parameters.get("text", "")))
    user_id = sanitize_user_id(parameters.get("user_id", f"agenthub_{request_id}"))

    if not message:
        return {"result": "No message provided.", "data": {}}

    # Input validation
    validation = validate_input(message, user_id=user_id)
    if not validation.is_valid:
        return {"result": "Invalid input.", "data": {"reason": validation.reason}}

    # Payment check for premium skills
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
            return JSONResponse(status_code=402, content=payment_info)

    initial_state = {
        "user_id": user_id,
        "platform": "agenthub",
        "raw_message": validation.sanitized or message,
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

        # Output validation
        output_validation = validate_output(result, skill_id=skill_id)
        result = output_validation.sanitized or result

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

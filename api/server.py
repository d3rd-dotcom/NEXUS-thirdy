"""
NEXUS-thirdy | api/server.py
Phase 9 — Multi-Platform FastAPI Server

FIXED (H3): Rate limiting via slowapi — configurable per-IP per-minute limit
            (default 20, set via RATE_LIMIT_PER_MINUTE env var). The `request`
            parameter is required as the first arg on every rate-limited
            endpoint — slowapi reads the client IP from it.

FIXED (H4): CORS middleware added with a configurable origin allowlist from
            settings.ALLOWED_ORIGINS. An empty list (the default) blocks all
            cross-origin requests, which is the safest default for an API that
            has no legitimate browser clients until explicitly configured.

FIXED (C3, C4): process_chat() and agent_skill_call() now use
                make_initial_state() to build the LangGraph initial state,
                guaranteeing all 15 ThirdyState keys are always present.

FIXED (H8): LANGCHAIN_TRACING_V2 defaults to false in settings.py — no
            change needed here, the environment variable drives the SDK.
"""

import time
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware  # FIXED (H4)
from pydantic import BaseModel

# FIXED (H3): slowapi rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config.settings import settings
from config.skill_registry import (
    generate_skill_manifest,
    skill_count,
    get_skill,
    SKILL_REGISTRY,
)
from agent.graph import nexus_graph
from agent.state_factory import make_initial_state  # FIXED (C3, C4)
from platforms.pinai import pinai_polling_loop
from platforms.fetchai import fetchai_polling_loop
from platforms.webhook import normalize_webhook, detect_platform
from payments.x402_middleware import verify_payment, build_payment_required_response
from payments.wallet import get_wallet_address, get_balance
from security.validators import validate_input, validate_output, sanitize_user_id
import structlog

log = structlog.get_logger()
START_TIME = time.time()


# ── RATE LIMITER ──────────────────────────────────────────────────────────────
# FIXED (H3): Instantiated before the app so decorators can reference it.
# key_func=get_remote_address uses the client IP as the rate-limit bucket.
limiter = Limiter(key_func=get_remote_address)


# ── LIFESPAN ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("nexus_starting", environment=settings.ENVIRONMENT)
    tasks = [
        asyncio.create_task(pinai_polling_loop()),
        asyncio.create_task(fetchai_polling_loop()),
    ]
    log.info("platform_tasks_launched", count=len(tasks))
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("platform_tasks_stopped")


# ── APP ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="NEXUS-thirdy",
    description="Server-native AI agent. No laptop required.",
    version="0.9.1",
    lifespan=lifespan,
)

# FIXED (H3): Register the rate-limit exception handler so slowapi returns
# a proper 429 JSON response rather than an unhandled 500.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# FIXED (H4): CORS middleware with explicit allowlist.
# settings.ALLOWED_ORIGINS is an empty list by default, which blocks all
# cross-origin requests until the operator deliberately configures origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


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


class AgentSkillRequest(BaseModel):
    skill: str
    parameters: dict = {}
    request_id: str = ""


# ── CORE PROCESSING ───────────────────────────────────────────────────────────

async def process_chat(
    user_id: str,
    message: str,
    platform: str,
    payment_proof: str = "",
) -> ChatResponse:
    """
    Core processing function shared by all endpoints.

    FIXED (C3, C4): Uses make_initial_state() — all 15 ThirdyState keys are
    guaranteed present. The previous inline dict was missing payment_proof,
    reasoning_trace, reflexion_score, reflexion_iteration, and
    reflexion_critique, causing KeyError crashes in downstream nodes.

    Note: callers that need to return an HTTP error on invalid input
    (e.g. /chat) should validate BEFORE calling this function and raise
    HTTPException themselves. This function is an internal workhorse that
    trusts its inputs are pre-screened.
    """
    # FIXED (C3, C4): Factory guarantees all ThirdyState keys are present
    initial_state = make_initial_state(
        user_id=user_id,
        message=message,
        platform=platform,
        payment_proof=payment_proof,
    )

    try:
        final_state = await nexus_graph.ainvoke(initial_state)
    except Exception as e:
        log.error("graph_error", user_id=user_id, platform=platform, error=str(e))
        return ChatResponse(
            response="I encountered an issue. Please try again.",
            skill_used="error",
            cost_usdc=0.0,
        )

    skill_used = final_state.get("detected_skill", "unknown")
    raw_response = final_state.get("final_response", "")
    output_validation = validate_output(raw_response, skill_id=skill_used)
    final_response = output_validation.sanitized or raw_response

    skill_def = get_skill(skill_used)
    cost = (
        skill_def.price_usdc
        if skill_def and final_state.get("payment_verified")
        else 0.0
    )

    return ChatResponse(
        response=final_response,
        skill_used=skill_used,
        cost_usdc=cost,
    )


# ── DRIFT LOGGING ─────────────────────────────────────────────────────────────

async def log_interaction(
    user_id: str,
    skill: str,
    platform: str,
    success: bool,
) -> None:
    """Write an interaction record to Supabase for the weekly audit."""
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
            "timestamp": time.time(),
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
        "version": "0.9.1",
        "phase": "9 - multi-platform deployment",
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": int(time.time() - START_TIME),
        "skills": counts,
        "platforms": {
            "pinai": settings.has_pinai(),
            "fetchai": settings.has_fetchai(),
            "webhook": True,
            "agenthub": True,
            "mcp": True,
        },
        "llm": {
            "groq": settings.has_groq(),
            "nvidia": settings.has_nvidia(),
            "cerebras": settings.has_cerebras(),
        },
        "payments_ready": bool(settings.AGENT_WALLET_ADDRESS),
        "payment_verification": settings.X402_VERIFY_PAYMENTS,
        "security": {
            "firewall": settings.LLAMAFIREWALL_ENABLED,
            "input_validation": True,
            "output_validation": True,
            "rate_limit_per_minute": settings.RATE_LIMIT_PER_MINUTE,
            "cors_origins_configured": len(settings.ALLOWED_ORIGINS),
        },
        "wallet": wallet_address or "not configured",
        "memory_ready": {
            "vector": bool(settings.SUPABASE_URL and settings.SUPABASE_PROJECT_REF),
            "graph": bool(settings.GRAPHITI_NEO4J_URI),
        },
    }


@app.get("/platforms")
async def platforms():
    return {
        "active": [
            p for p, active in {
                "pinai": settings.has_pinai(),
                "fetchai": settings.has_fetchai(),
                "webhook": True,
                "agenthub": True,
                "mcp": True,
            }.items()
            if active
        ],
        "webhook_url": "https://nexus-thirdy.onrender.com/webhook",
        "agent_url": "https://nexus-thirdy.onrender.com/agent",
        "mcp_url": "https://nexus-thirdy.onrender.com/mcp",
        "chat_url": "https://nexus-thirdy.onrender.com/chat",
    }


@app.get("/skill.md", response_class=PlainTextResponse)
async def skill_manifest():
    return generate_skill_manifest()


@app.get("/wallet")
async def wallet_info():
    address = await get_wallet_address()
    balance = await get_balance()
    return {
        "address": address,
        "balance": balance,
        "network": settings.X402_NETWORK,
    }


# ── RATE-LIMITED ENDPOINTS ────────────────────────────────────────────────────
# FIXED (H3): `request: Request` MUST be the first parameter on every
# rate-limited endpoint — slowapi reads the client IP from it.

@app.post("/chat", response_model=ChatResponse)
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
async def chat(request: Request, req: ChatRequest):
    """
    Main chat endpoint. Returns 400 for empty or invalid input, 429 when the
    per-IP rate limit is exceeded, and a ChatResponse on success.
    """
    user_id = sanitize_user_id(req.user_id)
    validation = validate_input(req.message, user_id=user_id)

    if not validation.is_valid:
        # FIXED: Return 400 rather than silently returning a blocked ChatResponse.
        # This is the correct HTTP behaviour for a malformed client request and
        # also what the test suite asserts.
        raise HTTPException(status_code=400, detail=validation.reason)

    result = await process_chat(
        user_id=user_id,
        message=validation.sanitized or req.message,
        platform=req.platform,
        payment_proof=req.payment_proof or "",
    )

    asyncio.create_task(
        log_interaction(user_id, result.skill_used, req.platform, True)
    )
    return result


@app.post("/webhook")
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
async def webhook(request: Request):
    """
    Multi-platform webhook endpoint with automatic platform detection.
    Normalizes payloads from MindStudio, toku, Zapier, PIN AI, Fetch.AI,
    and generic REST integrations.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})

    headers = dict(request.headers)
    platform = detect_platform(headers, body)
    user_id, message = normalize_webhook(body, platform)

    if not message:
        return {"status": "empty_message", "platform": platform}

    result = await process_chat(
        user_id=user_id,
        message=message,
        platform=platform,
    )

    return {
        "response": result.response,
        "skill_used": result.skill_used,
        "cost_usdc": result.cost_usdc,
        "platform": platform,
    }


@app.post("/agent")
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
async def agent_skill_call(request: Request, req: AgentSkillRequest):
    """
    AgentHub + A2A (agent-to-agent) skill call endpoint.
    Supports x402 payment via the X-Payment request header.
    """
    skill_id = req.skill
    parameters = req.parameters
    request_id = req.request_id

    log.info("agent_skill_called", skill=skill_id, request_id=request_id)

    message = parameters.get(
        "message",
        parameters.get("query", parameters.get("text", "")),
    )
    user_id = sanitize_user_id(parameters.get("user_id", f"agent_{request_id}"))

    if not message:
        return {"result": "No message provided.", "data": {}}

    validation = validate_input(message, user_id=user_id)
    if not validation.is_valid:
        return {"result": "Invalid input.", "data": {"reason": validation.reason}}

    # Payment gate for premium skills called via the agent endpoint
    skill_def = get_skill(skill_id)
    if skill_def and skill_def.requires_payment:
        payment_proof = request.headers.get("X-Payment", "")
        wallet = await get_wallet_address()
        is_paid, reason = await verify_payment(
            skill_id=skill_id,
            payment_proof=payment_proof,
            user_address=user_id,
        )
        if not is_paid:
            return JSONResponse(
                status_code=402,
                content=build_payment_required_response(skill_id, wallet),
            )

    # FIXED (C3, C4): make_initial_state() via process_chat()
    result = await process_chat(
        user_id=user_id,
        message=validation.sanitized or message,
        platform="agenthub",
        payment_proof=request.headers.get("X-Payment", ""),
    )

    return {
        "result": result.response,
        "data": {
            "skill_used": result.skill_used,
            "request_id": request_id,
            "cost_usdc": result.cost_usdc,
        },
    }


@app.get("/mcp")
async def mcp_manifest():
    """
    MCP (Model Context Protocol) server manifest.
    Makes NEXUS-thirdy discoverable by any MCP-compatible agent or platform.
    """
    return {
        "schema_version": "1.0",
        "name": "NEXUS-thirdy",
        "description": (
            "AI agent with hybrid memory, crypto intelligence, "
            "and autonomous payments."
        ),
        "endpoint": "https://nexus-thirdy.onrender.com",
        "tools": [
            {
                "name": skill.id,
                "description": skill.description,
                "price_usdc": skill.price_usdc,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Your query or request",
                        }
                    },
                    "required": ["message"],
                },
            }
            for skill in SKILL_REGISTRY.values()
        ],
        "payment": {
            "protocol": "x402",
            "network": settings.X402_NETWORK,
            "recipient": settings.AGENT_WALLET_ADDRESS or "not configured",
        },
    }


@app.post("/mcp/call")
@limiter.limit(f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
async def mcp_call(request: Request):
    """MCP tool call endpoint — accepts calls from any MCP-compatible agent."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})

    tool_input = body.get("input", {})
    message = tool_input.get("message", "")
    user_id = sanitize_user_id(body.get("caller_id", "mcp_caller"))

    if not message:
        return {"content": [{"type": "text", "text": "No message provided."}]}

    result = await process_chat(
        user_id=user_id,
        message=message,
        platform="mcp",
    )

    return {
        "content": [{"type": "text", "text": result.response}],
        "tool_used": result.skill_used,
        "cost_usdc": result.cost_usdc,
    }

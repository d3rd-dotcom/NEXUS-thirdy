"""
NEXUS-thirdy | api/server.py
Phase 9 — Multi-Platform Server

Changes from Phase 8:
  - Fetch.AI polling loop added to lifespan
  - Webhook adapter uses platform auto-detection
  - MCP-compatible /mcp endpoint added
  - /platforms endpoint shows all connected platforms
  - Updated version to 0.9.0
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
from platforms.fetchai import fetchai_polling_loop
from platforms.webhook import normalize_webhook, detect_platform
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

    # Launch all platform polling loops as background tasks
    tasks = [
        asyncio.create_task(pinai_polling_loop()),
        asyncio.create_task(fetchai_polling_loop()),
    ]
    log.info("platform_tasks_launched", count=len(tasks))

    yield

    # Clean shutdown
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("platform_tasks_stopped")


# ── APP ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="NEXUS-thirdy",
    description="Server-native AI agent. No laptop required.",
    version="0.9.0",
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


# ── CORE PROCESSING ───────────────────────────────────────────────────────────

async def process_chat(
    user_id: str,
    message: str,
    platform: str,
    payment_proof: str = ""
) -> ChatResponse:
    """
    Core processing function shared by all endpoints.
    Validates input, runs LangGraph, validates output.
    """
    user_id = sanitize_user_id(user_id)
    validation = validate_input(message, user_id=user_id)

    if not validation.is_valid:
        return ChatResponse(
            response="Your message couldn't be processed. Please try rephrasing.",
            skill_used="blocked",
            cost_usdc=0.0
        )

    initial_state = {
        "user_id": user_id,
        "platform": platform,
        "raw_message": validation.sanitized or message.strip(),
        "detected_skill": "",
        "requires_payment": False,
        "payment_verified": False,
        "payment_proof": payment_proof,
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
        log.error("graph_error", user_id=user_id, platform=platform, error=str(e))
        return ChatResponse(
            response="I encountered an issue. Please try again.",
            skill_used="error",
            cost_usdc=0.0
        )

    skill_used = final_state.get("detected_skill", "unknown")
    raw_response = final_state.get("final_response", "")
    output_validation = validate_output(raw_response, skill_id=skill_used)
    final_response = output_validation.sanitized or raw_response

    skill_def = get_skill(skill_used)
    cost = skill_def.price_usdc if skill_def and final_state.get("payment_verified") else 0.0

    return ChatResponse(response=final_response, skill_used=skill_used, cost_usdc=cost)


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
        "version": "0.9.0",
        "phase": "9 - multi-platform deployment",
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": int(time.time() - START_TIME),
        "skills": counts,
        "platforms": {
            "pinai": settings.has_pinai(),
            "fetchai": settings.has_fetchai(),
            "webhook": True,
            "agenthub": True,
            "mcp": True
        },
        "llm": {
            "groq": settings.has_groq(),
            "nvidia": settings.has_nvidia(),
            "cerebras": settings.has_cerebras()
        },
        "payments_ready": bool(settings.AGENT_WALLET_ADDRESS),
        "security": {
            "firewall": settings.LLAMAFIREWALL_ENABLED,
            "input_validation": True,
            "output_validation": True
        },
        "wallet": wallet_address or "not configured",
        "memory_ready": {
            "vector": bool(settings.SUPABASE_URL),
            "graph": bool(settings.GRAPHITI_NEO4J_URI)
        }
    }


@app.get("/platforms")
async def platforms():
    """Shows all platforms NEXUS-thirdy is connected to."""
    return {
        "active": [
            p for p, active in {
                "pinai": settings.has_pinai(),
                "fetchai": settings.has_fetchai(),
                "webhook": True,
                "agenthub": True,
                "mcp": True
            }.items() if active
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
    return {"address": address, "balance": balance, "network": settings.X402_NETWORK}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    return await process_chat(
        user_id=req.user_id,
        message=req.message,
        platform=req.platform,
        payment_proof=req.payment_proof or ""
    )


@app.post("/webhook")
async def webhook(request: Request):
    """
    Multi-platform webhook endpoint.
    Auto-detects platform and normalizes payload.
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
        platform=platform
    )

    return {
        "response": result.response,
        "skill_used": result.skill_used,
        "cost_usdc": result.cost_usdc,
        "platform": platform
    }


@app.post("/agent")
async def agent_skill_call(req: AgentSkillRequest, request: Request):
    """AgentHub + A2A skill call endpoint."""
    skill_id = req.skill
    parameters = req.parameters
    request_id = req.request_id

    log.info("agent_skill_called", skill=skill_id, request_id=request_id)

    message = parameters.get("message", parameters.get("query", parameters.get("text", "")))
    user_id = sanitize_user_id(parameters.get("user_id", f"agent_{request_id}"))

    if not message:
        return {"result": "No message provided.", "data": {}}

    validation = validate_input(message, user_id=user_id)
    if not validation.is_valid:
        return {"result": "Invalid input.", "data": {"reason": validation.reason}}

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
            return JSONResponse(
                status_code=402,
                content=build_payment_required_response(skill_id, wallet)
            )

    result = await process_chat(
        user_id=user_id,
        message=validation.sanitized or message,
        platform="agenthub",
        payment_proof=request.headers.get("X-Payment", "")
    )

    return {
        "result": result.response,
        "data": {
            "skill_used": result.skill_used,
            "request_id": request_id,
            "cost_usdc": result.cost_usdc
        }
    }


@app.get("/mcp")
async def mcp_manifest():
    """
    MCP (Model Context Protocol) server manifest.
    Makes NEXUS-thirdy discoverable by any MCP-compatible agent or platform.
    Claude, GPT, Gemini agents can all discover and call NEXUS-thirdy skills
    through this endpoint.
    """
    from config.skill_registry import SKILL_REGISTRY
    return {
        "schema_version": "1.0",
        "name": "NEXUS-thirdy",
        "description": "AI agent with hybrid memory, crypto intelligence, and autonomous payments.",
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
                            "description": "Your query or request"
                        }
                    },
                    "required": ["message"]
                }
            }
            for skill in SKILL_REGISTRY.values()
        ],
        "payment": {
            "protocol": "x402",
            "network": settings.X402_NETWORK,
            "recipient": settings.AGENT_WALLET_ADDRESS or "not configured"
        }
    }


@app.post("/mcp/call")
async def mcp_call(request: Request):
    """
    MCP tool call endpoint.
    Any MCP-compatible agent sends tool calls here.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})

    tool_name = body.get("name", "")
    tool_input = body.get("input", {})
    message = tool_input.get("message", "")
    user_id = sanitize_user_id(body.get("caller_id", "mcp_caller"))

    if not message:
        return {"content": [{"type": "text", "text": "No message provided."}]}

    result = await process_chat(
        user_id=user_id,
        message=message,
        platform="mcp"
    )

    return {
        "content": [{"type": "text", "text": result.response}],
        "tool_used": result.skill_used,
        "cost_usdc": result.cost_usdc
    }

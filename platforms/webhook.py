"""
NEXUS-thirdy | platforms/webhook.py
Phase 9 — Generic Webhook Adapter

Normalizes incoming webhooks from different platforms into
the standard ChatRequest format for the LangGraph.

Supported platform formats:
  - MindStudio
  - toku.agency
  - Generic REST (any platform sending JSON)
  - Zapier / Make.com automations
  - Custom integrations

Each platform sends a slightly different JSON shape.
This adapter handles the differences so the LangGraph
receives a clean, consistent message every time.
"""

from security.validators import sanitize_user_id
import structlog

log = structlog.get_logger()


def normalize_webhook(body: dict, platform: str) -> tuple[str, str]:
    """
    Normalize a webhook payload to (user_id, message).
    Returns ("", "") if the payload cannot be parsed.

    Add new platform formats here as NEXUS-thirdy expands.
    """

    # ── MindStudio ────────────────────────────────────────────────────────────
    if platform == "mindstudio":
        user_id = body.get("userId", body.get("user_id", ""))
        message = (
            body.get("input", "") or
            body.get("message", "") or
            body.get("userMessage", "") or
            body.get("prompt", "")
        )
        return sanitize_user_id(user_id), message

    # ── toku.agency ───────────────────────────────────────────────────────────
    if platform == "toku":
        user_id = body.get("client_id", body.get("user_id", "toku_user"))
        message = body.get("task", body.get("message", body.get("instruction", "")))
        return sanitize_user_id(user_id), message

    # ── Zapier / Make.com ─────────────────────────────────────────────────────
    if platform in ("zapier", "make"):
        user_id = body.get("user_id", body.get("email", "automation_user"))
        message = body.get("message", body.get("text", body.get("input", "")))
        return sanitize_user_id(user_id), message

    # ── PIN AI (direct webhook, not polling) ──────────────────────────────────
    if platform == "pinai":
        user_id = body.get("from_agent_id", body.get("sender", "pinai_user"))
        message = body.get("content", body.get("message", ""))
        return sanitize_user_id(user_id), message

    # ── Fetch.AI ──────────────────────────────────────────────────────────────
    if platform == "fetchai":
        user_id = body.get("sender", body.get("from", "fetchai_agent"))
        message = body.get("payload", {}).get("text", body.get("content", body.get("message", "")))
        return sanitize_user_id(f"fetchai_{user_id}"), message

    # ── Generic fallback ──────────────────────────────────────────────────────
    # Try common field names in order of likelihood
    user_id = (
        body.get("user_id") or
        body.get("userId") or
        body.get("from") or
        body.get("sender") or
        body.get("client_id") or
        "webhook_user"
    )

    message = (
        body.get("message") or
        body.get("content") or
        body.get("text") or
        body.get("input") or
        body.get("query") or
        body.get("prompt") or
        ""
    )

    if not message:
        log.warning("webhook_parse_failed", platform=platform, keys=list(body.keys()))

    return sanitize_user_id(user_id), message


def detect_platform(headers: dict, body: dict) -> str:
    """
    Auto-detect the platform from request headers or body structure.
    Falls back to "generic" if unknown.
    """
    # Check explicit header first
    platform_header = headers.get("x-platform", headers.get("X-Platform", ""))
    if platform_header:
        return platform_header.lower()

    # Detect by body structure
    if "from_agent_id" in body:
        return "pinai"
    if "userMessage" in body or "userId" in body:
        return "mindstudio"
    if "task" in body and "client_id" in body:
        return "toku"
    if "zap_id" in body:
        return "zapier"
    if "sender" in body and "payload" in body:
        return "fetchai"

    return "generic"

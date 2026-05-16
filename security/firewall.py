"""
NEXUS-thirdy | security/firewall.py
Phase 8 — LlamaFirewall Prompt Injection Defense

Scans all externally-retrieved content before it enters the reasoning chain.
Protects against indirect prompt injection — malicious instructions
embedded in web pages, documents, tool outputs, or user messages.

LlamaFirewall is Meta's open-source guardrail system (2026).
Components used:
  - PromptGuard 2: classifies injection attempts
  - AlignmentCheck: audits reasoning chain for hijacking

Fails open — if firewall errors, content passes through.
An agent that randomly blocks legitimate content is worse than one
that occasionally passes injection attempts.
"""

from config.settings import settings
import structlog

log = structlog.get_logger()

_firewall = None


def _get_firewall():
    """Lazy initialization — only load if enabled."""
    global _firewall

    if _firewall is not None:
        return _firewall

    if not settings.LLAMAFIREWALL_ENABLED:
        return None

    try:
        from llamafirewall import LlamaFirewall
        _firewall = LlamaFirewall()
        log.info("llamafirewall_loaded")
        return _firewall
    except ImportError:
        log.warning("llamafirewall_not_installed", hint="pip install llamafirewall")
        return None
    except Exception as e:
        log.warning("llamafirewall_init_failed", error=str(e))
        return None


async def scan_content(content: str, context: str = "user_input") -> tuple[bool, str]:
    """
    Scan content for prompt injection attempts.
    Returns (is_safe: bool, reason: str).

    Use this wrapper around:
    - User messages (before LLM call)
    - Web scraping results
    - Tool outputs containing external data
    - Document contents

    Fails open on errors — returns (True, "scan_error") so agent keeps running.
    """
    if not content or not content.strip():
        return True, "empty_content"

    fw = _get_firewall()
    if fw is None:
        return True, "firewall_disabled"

    try:
        from llamafirewall import ScanDecision

        result = fw.scan(content)

        if result.decision == ScanDecision.BLOCK:
            log.warning(
                "injection_blocked",
                context=context,
                reason=result.reason,
                content_preview=content[:100]
            )
            return False, result.reason

        return True, "clean"

    except Exception as e:
        log.error("firewall_scan_error", error=str(e))
        return True, "scan_error_passthrough"


async def scan_message(user_id: str, message: str) -> tuple[bool, str]:
    """
    Convenience wrapper for scanning user messages specifically.
    Logs user_id for audit trail.
    """
    is_safe, reason = await scan_content(message, context="user_message")

    if not is_safe:
        log.warning(
            "user_message_blocked",
            user_id=user_id,
            reason=reason
        )

    return is_safe, reason

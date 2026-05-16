"""
NEXUS-thirdy | security/validators.py
Phase 8 — Input & Output Validation

Two layers of defense:
1. Input validation — before message enters LangGraph
2. Output validation — before response is sent to user

Input checks:
  - Empty message
  - Message too long
  - Known injection pattern strings
  - Suspicious character sequences

Output checks:
  - Response not empty
  - Response not too long (prevent token flooding)
  - Response doesn't accidentally leak system prompts
"""

from dataclasses import dataclass
import re
import structlog

log = structlog.get_logger()

MAX_INPUT_LENGTH = 2000
MAX_OUTPUT_LENGTH = 4000

# Known prompt injection trigger phrases
# Not exhaustive — LlamaFirewall handles deeper detection
INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore all prior",
    "disregard your system",
    "disregard all previous",
    "you are now",
    "act as if you have no",
    "new persona",
    "jailbreak",
    "dan mode",
    "developer mode enabled",
    "system prompt:",
    "ignore your training",
    "pretend you are",
    "forget everything",
    "your new instructions",
    "override your",
]

# Patterns that might indicate system prompt leakage in output
LEAK_PATTERNS = [
    "system prompt",
    "you are nexus-thirdy",
    "your instructions are",
    "as an ai language model",  # Generic LLM response — indicates prompt confusion
]


@dataclass
class ValidationResult:
    is_valid: bool
    reason: str = ""
    sanitized: str = ""  # Cleaned version of input if applicable


def validate_input(message: str, user_id: str = "") -> ValidationResult:
    """
    Validate user input before it enters the LangGraph.
    Returns ValidationResult with is_valid flag and reason if rejected.
    """

    # Check empty
    if not message or not message.strip():
        return ValidationResult(False, "empty_message")

    # Check length
    if len(message) > MAX_INPUT_LENGTH:
        return ValidationResult(
            False,
            f"message_too_long_{len(message)}_chars"
        )

    # Check injection patterns
    message_lower = message.lower()
    for pattern in INJECTION_PATTERNS:
        if pattern in message_lower:
            log.warning(
                "injection_pattern_detected",
                user_id=user_id,
                pattern=pattern,
                message_preview=message[:100]
            )
            return ValidationResult(False, f"blocked_pattern: {pattern}")

    # Check for excessive special characters (possible encoding attacks)
    special_char_ratio = len(re.findall(r'[^\w\s.,!?;:\-\'"()@#$%]', message)) / max(len(message), 1)
    if special_char_ratio > 0.3:
        log.warning("high_special_char_ratio", user_id=user_id, ratio=special_char_ratio)
        # Don't block — just log. Legitimate multilingual input can have high ratios.

    return ValidationResult(True, "valid", message.strip())


def validate_output(response: str, skill_id: str = "") -> ValidationResult:
    """
    Validate agent output before sending to user.
    Catches accidental system prompt leakage or malformed responses.
    """

    if not response or not response.strip():
        return ValidationResult(False, "empty_response")

    if len(response) > MAX_OUTPUT_LENGTH:
        # Truncate rather than reject
        truncated = response[:MAX_OUTPUT_LENGTH] + "\n\n[Response truncated]"
        log.warning("output_truncated", skill=skill_id, original_length=len(response))
        return ValidationResult(True, "truncated", truncated)

    # Check for system prompt leakage
    response_lower = response.lower()
    for pattern in LEAK_PATTERNS:
        if pattern in response_lower:
            log.warning(
                "potential_leak_detected",
                skill=skill_id,
                pattern=pattern
            )
            # Don't block — log for review. Some patterns are legitimate.
            break

    return ValidationResult(True, "valid", response)


def sanitize_user_id(user_id: str) -> str:
    """
    Sanitize user_id for safe use as a database key.
    Removes characters that could cause SQL injection or path traversal.
    """
    if not user_id:
        return "anonymous"

    # Keep alphanumeric, hyphens, underscores, dots, spaces
    sanitized = re.sub(r'[^\w\s\-.]', '', user_id)
    sanitized = sanitized.strip()[:100]  # Max 100 chars

    return sanitized if sanitized else "anonymous"

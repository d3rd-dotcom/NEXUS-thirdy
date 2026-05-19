"""
NEXUS-thirdy | config/settings.py
Single source of truth for all environment variables.
Every other file imports from here — nothing reads os.environ directly except this file.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:

    # --- LLM API KEYS ---
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
    NVIDIA_API_KEY: str = os.environ.get("NVIDIA_API_KEY", "")
    CEREBRAS_API_KEY: str = os.environ.get("CEREBRAS_API_KEY", "")
    MISTRAL_API_KEY: str = os.environ.get("MISTRAL_API_KEY", "")
    COHERE_API_KEY: str = os.environ.get("COHERE_API_KEY", "")

    # --- DATABASE ---
    SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY: str = os.environ.get("SUPABASE_SERVICE_KEY", "")

    # FIXED (M1): Supabase infrastructure details moved out of source code into env vars.
    # Previously hardcoded as "postgres.wmavdqyjkbbrocnagegd" and the pooler host
    # directly inside memory/mem0_store.py — a security and portability problem.
    SUPABASE_PROJECT_REF: str = os.environ.get("SUPABASE_PROJECT_REF", "")
    SUPABASE_POOLER_HOST: str = os.environ.get(
        "SUPABASE_POOLER_HOST", "aws-0-ap-southeast-1.pooler.supabase.com"
    )

    # --- MEMORY ---
    GRAPHITI_NEO4J_URI: str = os.environ.get("GRAPHITI_NEO4J_URI", "")
    GRAPHITI_NEO4J_USER: str = os.environ.get("GRAPHITI_NEO4J_USER", "")
    GRAPHITI_NEO4J_PASSWORD: str = os.environ.get("GRAPHITI_NEO4J_PASSWORD", "")

    # --- PAYMENTS ---
    CDP_API_KEY_NAME: str = os.environ.get("CDP_API_KEY_NAME", "")
    CDP_API_KEY_SECRET: str = os.environ.get("CDP_API_KEY_SECRET", "")
    X402_NETWORK: str = os.environ.get("X402_NETWORK", "base-sepolia")
    AGENT_WALLET_ADDRESS: str = os.environ.get("AGENT_WALLET_ADDRESS", "")

    # FIXED (C2): Toggle for real vs stub payment verification.
    # false (default) = stub mode, only "stub_test_" prefixed proofs accepted.
    # true            = full x402 cryptographic verification via Coinbase CDP.
    # NEVER leave false in production when accepting real USDC.
    X402_VERIFY_PAYMENTS: bool = os.environ.get("X402_VERIFY_PAYMENTS", "false") == "true"

    # --- PLATFORMS ---
    PINAI_API_KEY: str = os.environ.get("PINAI_API_KEY", "")
    PINAI_AGENT_ID: str = os.environ.get("PINAI_AGENT_ID", "")
    PINAI_API_URL: str = os.environ.get("PINAI_API_URL", "https://agents.pinai.tech")

    # FIXED (H6): These were missing from settings entirely; any code referencing
    # settings.FETCHAI_API_KEY etc. would raise AttributeError at runtime.
    FETCHAI_API_KEY: str = os.environ.get("FETCHAI_API_KEY", "")
    MINDSTUDIO_WEBHOOK_SECRET: str = os.environ.get("MINDSTUDIO_WEBHOOK_SECRET", "")
    TOKU_API_KEY: str = os.environ.get("TOKU_API_KEY", "")

    # --- OBSERVABILITY ---
    LANGCHAIN_API_KEY: str = os.environ.get("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT: str = os.environ.get("LANGCHAIN_PROJECT", "nexus-thirdy-dev")

    # FIXED (H8): Default changed to "false". Tracing sends all user messages to
    # LangSmith — a GDPR violation if enabled by default in production without
    # explicit user consent. Opt-in, not opt-out.
    LANGCHAIN_TRACING_V2: str = os.environ.get("LANGCHAIN_TRACING_V2", "false")

    # --- SECURITY ---
    LLAMAFIREWALL_ENABLED: bool = os.environ.get("LLAMAFIREWALL_ENABLED", "true") == "true"
    MAX_SPEND_PER_SESSION_USDC: float = float(
        os.environ.get("MAX_SPEND_PER_SESSION_USDC", "5.0")
    )

    # FIXED (H3): Rate limiting — configurable per deployment, defaults to 20/min per IP.
    RATE_LIMIT_PER_MINUTE: int = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "20"))

    # FIXED (H4): CORS allowlist — empty string means NO cross-origin access permitted.
    # Set to comma-separated origins in production, e.g.:
    #   ALLOWED_ORIGINS=https://your-app.com,https://app.mindstudio.ai
    ALLOWED_ORIGINS: list = [
        origin.strip()
        for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]

    # --- SERVER ---
    PORT: int = int(os.environ.get("PORT", "8000"))
    ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "development")

    # --- HELPERS ---

    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    def has_groq(self) -> bool:
        return bool(self.GROQ_API_KEY)

    def has_nvidia(self) -> bool:
        return bool(self.NVIDIA_API_KEY)

    # FIXED (H6): These helper methods were absent from config/__init__.py's duplicate
    # Settings class, causing AttributeError in api/server.py status endpoint.
    def has_cerebras(self) -> bool:
        return bool(self.CEREBRAS_API_KEY)

    def has_pinai(self) -> bool:
        return bool(self.PINAI_API_KEY and self.PINAI_AGENT_ID)

    def has_fetchai(self) -> bool:
        return bool(self.FETCHAI_API_KEY)

    def has_payments(self) -> bool:
        return bool(self.CDP_API_KEY_NAME and self.CDP_API_KEY_SECRET)


# Single instance imported everywhere
settings = Settings()

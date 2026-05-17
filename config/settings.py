"""
NEXUS-thirdy | config/settings.py
Phase 9 — Updated Settings

Added:
  - FETCHAI_API_KEY for Fetch.AI Agentverse
  - MINDSTUDIO_WEBHOOK_SECRET for MindStudio verification
  - TOKU_API_KEY for toku.agency
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

    # --- MEMORY ---
    GRAPHITI_NEO4J_URI: str = os.environ.get("GRAPHITI_NEO4J_URI", "")
    GRAPHITI_NEO4J_USER: str = os.environ.get("GRAPHITI_NEO4J_USER", "")
    GRAPHITI_NEO4J_PASSWORD: str = os.environ.get("GRAPHITI_NEO4J_PASSWORD", "")

    # --- PAYMENTS ---
    CDP_API_KEY_NAME: str = os.environ.get("CDP_API_KEY_NAME", "")
    CDP_API_KEY_SECRET: str = os.environ.get("CDP_API_KEY_SECRET", "")
    X402_NETWORK: str = os.environ.get("X402_NETWORK", "base-sepolia")
    AGENT_WALLET_ADDRESS: str = os.environ.get("AGENT_WALLET_ADDRESS", "")

    # --- PLATFORMS ---
    PINAI_API_KEY: str = os.environ.get("PINAI_API_KEY", "")
    PINAI_AGENT_ID: str = os.environ.get("PINAI_AGENT_ID", "")
    PINAI_API_URL: str = os.environ.get("PINAI_API_URL", "https://agents.pinai.tech")

    FETCHAI_API_KEY: str = os.environ.get("FETCHAI_API_KEY", "")
    MINDSTUDIO_WEBHOOK_SECRET: str = os.environ.get("MINDSTUDIO_WEBHOOK_SECRET", "")
    TOKU_API_KEY: str = os.environ.get("TOKU_API_KEY", "")

    # --- OBSERVABILITY ---
    LANGCHAIN_API_KEY: str = os.environ.get("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT: str = os.environ.get("LANGCHAIN_PROJECT", "nexus-thirdy-dev")
    LANGCHAIN_TRACING_V2: str = os.environ.get("LANGCHAIN_TRACING_V2", "false")

    # --- SECURITY ---
    LLAMAFIREWALL_ENABLED: bool = os.environ.get("LLAMAFIREWALL_ENABLED", "true") == "true"
    MAX_SPEND_PER_SESSION_USDC: float = float(os.environ.get("MAX_SPEND_PER_SESSION_USDC", "5.0"))

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

    def has_cerebras(self) -> bool:
        return bool(self.CEREBRAS_API_KEY)

    def has_pinai(self) -> bool:
        return bool(self.PINAI_API_KEY and self.PINAI_AGENT_ID)

    def has_fetchai(self) -> bool:
        return bool(self.FETCHAI_API_KEY)

    def has_payments(self) -> bool:
        return bool(self.CDP_API_KEY_NAME and self.CDP_API_KEY_SECRET)


settings = Settings()

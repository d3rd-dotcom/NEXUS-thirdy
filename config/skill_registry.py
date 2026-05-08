"""
NEXUS-thirdy | config/skill_registry.py
Phase 2 — Skill Registry

Master list of every skill NEXUS-thirdy has.
Defines: name, description, price, which LLM handles it, and whether it needs payment.

This is the single file you edit when adding new skills.
The /skill.md endpoint and the supervisor both read from here automatically.
"""

from dataclasses import dataclass


@dataclass
class Skill:
    id: str                  # unique key, used internally
    name: str                # human-readable name
    description: str         # shown in /skill.md and used by supervisor for routing
    price_usdc: float        # 0.0 = free
    llm_tier: str            # "fast" | "quality" | "premium"
    requires_payment: bool   # True = blocked until x402 payment confirmed


# ── FREE SKILLS ───────────────────────────────────────────────────────────────
# These run on Groq Llama 3.1 8B (fastest, free tier)
# No payment required. Always available.

FREE_SKILLS = {
    "greet": Skill(
        id="greet",
        name="Greeting",
        description="Warm introduction to NEXUS-thirdy, lists available skills",
        price_usdc=0.0,
        llm_tier="fast",
        requires_payment=False
    ),
    "crypto_price": Skill(
        id="crypto_price",
        name="Crypto Price Check",
        description="Real-time price for any cryptocurrency (BTC, ETH, SOL, etc.)",
        price_usdc=0.0,
        llm_tier="fast",
        requires_payment=False
    ),
    "weather": Skill(
        id="weather",
        name="Weather",
        description="Current weather conditions for any city or location",
        price_usdc=0.0,
        llm_tier="fast",
        requires_payment=False
    ),
    "wisdom": Skill(
        id="wisdom",
        name="Wisdom Quote",
        description="Curated insight from philosophy, stoicism, or tech culture",
        price_usdc=0.0,
        llm_tier="fast",
        requires_payment=False
    ),
    "joke": Skill(
        id="joke",
        name="Joke",
        description="Crypto or AI themed humor",
        price_usdc=0.0,
        llm_tier="fast",
        requires_payment=False
    ),
    "define": Skill(
        id="define",
        name="Define Term",
        description="Clear explanation of any crypto, Web3, or AI term",
        price_usdc=0.0,
        llm_tier="fast",
        requires_payment=False
    ),
    "agent_status": Skill(
        id="agent_status",
        name="Agent Status",
        description="NEXUS-thirdy's current uptime, skill count, and platform info",
        price_usdc=0.0,
        llm_tier="fast",
        requires_payment=False
    ),
    "flip": Skill(
        id="flip",
        name="Coin Flip",
        description="Random heads or tails decision maker",
        price_usdc=0.0,
        llm_tier="fast",
        requires_payment=False
    ),
    "translate": Skill(
        id="translate",
        name="Translate",
        description="Translate text between any two languages",
        price_usdc=0.0,
        llm_tier="fast",
        requires_payment=False
    ),
    "summarize": Skill(
        id="summarize",
        name="Summarize",
        description="Condense any text or article into key points",
        price_usdc=0.0,
        llm_tier="fast",
        requires_payment=False
    ),
}

# ── PREMIUM SKILLS ────────────────────────────────────────────────────────────
# These run on NVIDIA NIM 70B or Cerebras 70B (deeper reasoning)
# Require x402 USDC payment before execution.
# Output is scored by Reflexion node — retried if quality is below threshold.

PREMIUM_SKILLS = {
    "crypto_intelligence": Skill(
        id="crypto_intelligence",
        name="Crypto Intelligence Report",
        description=(
            "Deep analysis of any crypto asset: on-chain metrics, sentiment score, "
            "risk rating, and entry/exit signals. Includes data from multiple sources."
        ),
        price_usdc=0.25,
        llm_tier="premium",
        requires_payment=True
    ),
    "defi_yield_finder": Skill(
        id="defi_yield_finder",
        name="DeFi Yield Finder",
        description=(
            "Scans top DeFi protocols for highest-yield opportunities "
            "matched to your risk tolerance."
        ),
        price_usdc=0.50,
        llm_tier="premium",
        requires_payment=True
    ),
    "market_brief": Skill(
        id="market_brief",
        name="Market Brief",
        description=(
            "5-minute read: top 3 market movers today, macro context, "
            "and one actionable insight."
        ),
        price_usdc=0.10,
        llm_tier="quality",
        requires_payment=True
    ),
    "sentiment_scan": Skill(
        id="sentiment_scan",
        name="Sentiment Scanner",
        description=(
            "Real-time sentiment analysis for any crypto asset "
            "from X, Reddit, and on-chain activity."
        ),
        price_usdc=0.10,
        llm_tier="quality",
        requires_payment=True
    ),
    "portfolio_tracker": Skill(
        id="portfolio_tracker",
        name="Portfolio Tracker",
        description=(
            "Track your holdings across sessions, calculate P&L, "
            "and flag current risk exposure."
        ),
        price_usdc=0.15,
        llm_tier="premium",
        requires_payment=True
    ),
}

# ── COMBINED REGISTRY ─────────────────────────────────────────────────────────
SKILL_REGISTRY: dict[str, Skill] = {**FREE_SKILLS, **PREMIUM_SKILLS}

# ── LLM ROUTING MAP ───────────────────────────────────────────────────────────
# Maps llm_tier → actual model string used in LangChain/LangGraph calls
LLM_TIER_MAP = {
    "fast":    "groq/llama-3.1-8b-instant",       # Speed priority — routing + free skills
    "quality": "cerebras/llama-3.1-70b",           # Speed + quality — mid-tier premium
    "premium": "nvidia/llama-3.1-nemotron-70b-instruct",  # Highest reasoning — deep analysis
}


# ── HELPER FUNCTIONS ──────────────────────────────────────────────────────────

def get_skill(skill_id: str) -> Skill | None:
    return SKILL_REGISTRY.get(skill_id)

def get_free_skills() -> dict[str, Skill]:
    return FREE_SKILLS

def get_premium_skills() -> dict[str, Skill]:
    return PREMIUM_SKILLS

def skill_count() -> dict:
    return {
        "total": len(SKILL_REGISTRY),
        "free": len(FREE_SKILLS),
        "premium": len(PREMIUM_SKILLS),
    }


# ── SKILL MANIFEST (for /skill.md endpoint) ───────────────────────────────────

def generate_skill_manifest() -> str:
    """
    Auto-generates the /skill.md content from the registry.
    Called by the FastAPI /skill.md endpoint.
    Always up to date — edit SKILL_REGISTRY, manifest updates automatically.
    """
    lines = [
        "# NEXUS-thirdy\n",
        "> Intelligent AI agent with hybrid memory, crypto intelligence, and autonomous payments.\n\n",
        "---\n\n",
        "## Free Skills\n\n",
    ]

    for skill in FREE_SKILLS.values():
        lines.append(f"### {skill.name}\n")
        lines.append(f"{skill.description}\n\n")

    lines.append("---\n\n")
    lines.append("## Premium Skills (USDC)\n\n")

    for skill in PREMIUM_SKILLS.values():
        lines.append(f"### {skill.name} — {skill.price_usdc} USDC\n")
        lines.append(f"{skill.description}\n\n")

    lines.append("---\n\n")
    lines.append("## Agent Info\n\n")
    lines.append("- **Built by:** Leonardo Amora III (@thirdy12356)\n")
    lines.append("- **Platform:** Render (always-on, server-native)\n")
    lines.append(f"- **Total skills:** {len(SKILL_REGISTRY)} "
                 f"({len(FREE_SKILLS)} free, {len(PREMIUM_SKILLS)} premium)\n")

    return "".join(lines)

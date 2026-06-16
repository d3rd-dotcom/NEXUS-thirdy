# NEXUS-thirdy

> Server-native AI agent. Built from scratch. Deployed on Render. No laptop required.

---

## What NEXUS-thirdy is

NEXUS-thirdy is a production-grade AI agent with:

- **LangGraph** orchestration with crash recovery and reflexion self-evaluation
- **Hybrid memory** — Mem0 vector store + Graphiti temporal knowledge graph on Supabase
- **Reflexion** self-evaluation on every premium output (scored 1-10, retried if < 7)
- **x402** autonomous USDC micropayments on Base via Coinbase CDP
- **LlamaFirewall** prompt injection defence on every incoming message
- **Multi-platform** deployment from one permanent Render URL
- **Rate limiting** — slowapi, configurable per-IP per-minute (default 20 req/min)
- **CORS** allowlist via `ALLOWED_ORIGINS` environment variable
- **Payment verification toggle** — stub mode for dev, full x402 verification for prod

---

## Project Structure

```
nexus/
├── agent/
│   ├── state_factory.py    ← Centralised initial-state factory (all keys guaranteed)
│   ├── graph.py            ← LangGraph brain — nodes, edges, routing
│   ├── supervisor.py       ← Routing node (Groq 8B + Cerebras fallback, lazy init)
│   ├── reflexion.py        ← Quality critic node (lazy init)
│   └── nodes/
│       ├── free_skills.py  ← Free skill execution (lazy init, cached counts)
│       └── premium_skills.py ← Premium skill execution (cached LLM clients)
├── memory/
│   ├── mem0_store.py       ← Mem0 vector memory (executor for blocking I/O)
│   ├── graphiti_store.py   ← Graphiti knowledge graph (auto-reconnect on failure)
│   └── context_builder.py  ← Assembles context pack before every LLM call
├── payments/
│   ├── x402_middleware.py  ← x402 payment verification (bypass closed, toggle support)
│   └── wallet.py           ← Coinbase AgentKit MPC wallet
├── security/
│   ├── firewall.py         ← LlamaFirewall prompt injection defence
│   └── validators.py       ← Input/output validation (narrowed injection patterns)
├── platforms/
│   ├── pinai.py            ← PIN AI AgentHub polling (TTL cache, state factory)
│   ├── fetchai.py          ← Fetch.AI Agentverse polling (state factory)
│   └── webhook.py          ← Generic multi-platform webhook adapter
├── api/
│   └── server.py           ← FastAPI server (rate limiting, CORS, all endpoints)
├── config/
│   ├── __init__.py         ← Empty (duplicate Settings class removed)
│   ├── settings.py         ← Single source of truth for all env vars
│   └── skill_registry.py   ← Master skill list (free + premium)
├── scripts/
│   ├── setup_supabase.py   ← Prints SQL to run manually in Supabase SQL Editor
│   ├── init_wallet.py      ← One-time Coinbase AgentKit wallet creation
│   └── weekly_audit.py     ← Monday audit report from Supabase interaction logs
└── tests/
    └── test_core.py        ← Pytest suite (proper no-op lifespan fixture)
```

---

## Quick Start (Local Dev)

```bash
# 1. Clone and enter the repo
git clone https://github.com/d3rd-dotcom/NEXUS-thirdy.git
cd NEXUS-thirdy

# 2. Create your .env from the template
cp .env.example .env
# Open .env and fill in your API keys

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up Supabase tables (prints SQL — run it manually in the dashboard)
python scripts/setup_supabase.py

# 5. Start the server
uvicorn api.server:app --reload --port 8000
```

The agent will be available at `http://localhost:8000`.

---

## Deploy to Render

Push to `main` → GitHub Actions runs the test suite → Render auto-deploys.

```bash
git add .
git commit -m "your message"
git push origin main
```

**Render setup:**
1. Create a new **Web Service** on [render.com](https://render.com)
2. Connect your GitHub repo
3. Set **Build Command**: `pip install -r requirements.txt`
4. Set **Start Command**: `uvicorn api.server:app --host 0.0.0.0 --port $PORT`
5. Add all environment variables from `.env.example` under **Environment**

---

## Payment Verification

| `X402_VERIFY_PAYMENTS` | Behaviour |
|------------------------|-----------|
| `false` (default)      | Stub mode — only `stub_test_*` prefixed proofs accepted for local testing |
| `true`                 | Full x402 cryptographic verification via Coinbase CDP facilitator |

Set `X402_VERIFY_PAYMENTS=true` in Render before accepting real USDC payments.

> **Note:** In previous versions, any non-empty string in `payment_proof` bypassed payment entirely. This is fixed — `false` mode now only accepts the explicit `stub_test_` prefix.

---

## Key Environment Variables

See `.env.example` for the complete list with descriptions. Critical ones:

| Variable | Purpose |
|----------|---------|
| `SUPABASE_PROJECT_REF` | Your Supabase project reference ID |
| `SUPABASE_POOLER_HOST` | Supabase connection pooler host |
| `X402_VERIFY_PAYMENTS` | `true` = real payments, `false` = stub mode |
| `ALLOWED_ORIGINS` | Comma-separated CORS allowlist (empty = block all) |
| `RATE_LIMIT_PER_MINUTE` | Per-IP rate limit (default `20`) |
| `FETCHAI_API_KEY` | Fetch.AI Agentverse API key |
| `LANGCHAIN_TRACING_V2` | `false` by default (opt-in, not opt-out) |

---

## Running Tests

```bash
pytest tests/ -v --tb=short
```

Tests run automatically on every push via GitHub Actions (`.github/workflows/deploy.yml`) before the Render deploy hook is triggered.

---

## Live Endpoints

Once deployed, your agent is available at:

| Endpoint | Purpose |
|----------|---------|
| `GET  /health` | Health check (also accepts HEAD) |
| `GET  /status` | Full agent status JSON |
| `GET  /skill.md` | Auto-generated skill manifest |
| `POST /chat` | Main chat endpoint |
| `POST /webhook` | Multi-platform webhook (auto-detects source) |
| `POST /agent` | AgentHub + A2A skill calls |
| `GET  /mcp` | MCP server manifest |
| `POST /mcp/call` | MCP tool call endpoint |
| `GET  /wallet` | Wallet address and USDC balance |
| `GET  /platforms` | Connected platform list |

---

# NEXUS-thirdy

> Server-native AI agent. Built from scratch. Deployed on Koyeb. No laptop required.

---

## What NEXUS is

NEXUS is a production-grade AI agent with:
- **LangGraph** orchestration with crash recovery
- **Hybrid memory** (Mem0 vector + Graphiti knowledge graph on Supabase)
- **Reflexion** self-evaluation on every premium output
- **x402** autonomous USDC payments on Base
- **LlamaFirewall** prompt injection defense
- **Multi-platform** deployment from one permanent Koyeb URL

---

## Project Structure

```
nexus/
├── agent/          ← LangGraph brain (supervisor, nodes, reflexion)
├── memory/         ← Mem0 + Graphiti + context builder
├── payments/       ← x402 middleware + AgentKit wallet
├── security/       ← LlamaFirewall + input validators
├── platforms/      ← PIN AI, Fetch.AI, webhook connectors
├── api/            ← FastAPI server
├── config/         ← Settings + skill registry
├── scripts/        ← One-time setup scripts
└── tests/          ← Pytest test suite
```

---

## Quick Start (Local Dev)

```bash
cp .env.example .env
# Fill in your API keys in .env

pip install -r requirements.txt

uvicorn api.server:app --reload --port 8000
```

---

## Deploy

Push to `main` → GitHub Actions runs tests → Koyeb auto-deploys.

```bash
git add .
git commit -m "your message"
git push origin main
```

---

## Built by

Leonardo Amora III (@thirdy12356)

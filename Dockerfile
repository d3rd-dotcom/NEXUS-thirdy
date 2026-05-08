# ── BASE IMAGE ────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# ── ENVIRONMENT ───────────────────────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# ── SYSTEM DEPENDENCIES ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── WORKDIR ───────────────────────────────────────────────────────────────────
WORKDIR /app

# ── DEPENDENCIES ──────────────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi uvicorn[standard] python-dotenv

# ── APPLICATION CODE ──────────────────────────────────────────────────────────
COPY . .

# ── HEALTHCHECK ───────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# ── STARTUP ───────────────────────────────────────────────────────────────────
CMD uvicorn api.server:app --host 0.0.0.0 --port ${PORT} --workers 1

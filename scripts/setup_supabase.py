"""
NEXUS-thirdy | scripts/setup_supabase.py
Phase 8 — Supabase Table Setup

FIXED (H5): The previous version called `supabase.rpc("query", {"query": ...})`
            which is not a valid Supabase Python client method. It silently
            returned an error that was swallowed by the bare `except` block,
            so the nexus_interactions table was never actually created.
            Drift logging has been silently broken since Phase 8.

This script now prints the SQL you need to run manually in the Supabase
SQL Editor. This approach is reliable and does not require service-role
permissions to execute arbitrary DDL through the REST API.

Usage:
    python scripts/setup_supabase.py

Then:
    1. Open https://supabase.com/dashboard
    2. Select your project → SQL Editor → New Query
    3. Paste and run BLOCK 1, then BLOCK 2 (separately)
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── SQL BLOCKS ────────────────────────────────────────────────────────────────

# BLOCK 1: Enable pgvector extension (required by Mem0 for vector memory)
PGVECTOR_SQL = """\
-- ============================================================
-- BLOCK 1: Enable pgvector extension
-- Required by Mem0 to store and search embedding vectors.
-- Run this FIRST — the nexus_memories table depends on it.
-- ============================================================
CREATE EXTENSION IF NOT EXISTS vector;
"""

# BLOCK 2: Create the nexus_interactions table for drift logging
INTERACTIONS_SQL = """\
-- ============================================================
-- BLOCK 2: Create nexus_interactions table
-- Used by api/server.py log_interaction() for drift monitoring
-- and by scripts/weekly_audit.py for the Monday audit report.
-- ============================================================

CREATE TABLE IF NOT EXISTS nexus_interactions (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     TEXT         NOT NULL,
    skill       TEXT         NOT NULL,
    platform    TEXT         NOT NULL,
    success     BOOLEAN      NOT NULL DEFAULT TRUE,
    timestamp   FLOAT        NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Indexes for efficient audit queries (filter by user, skill, or time window)
CREATE INDEX IF NOT EXISTS idx_nexus_interactions_user_id
    ON nexus_interactions (user_id);

CREATE INDEX IF NOT EXISTS idx_nexus_interactions_skill
    ON nexus_interactions (skill);

CREATE INDEX IF NOT EXISTS idx_nexus_interactions_timestamp
    ON nexus_interactions (timestamp);

-- Optional: auto-purge rows older than 90 days to control table size.
-- Requires the pg_cron extension (available on Supabase Pro).
-- Uncomment to enable:
-- SELECT cron.schedule(
--     'purge-old-interactions',
--     '0 3 * * 0',
--     $$DELETE FROM nexus_interactions
--       WHERE created_at < NOW() - INTERVAL '90 days'$$
-- );
"""


# ── OUTPUT ────────────────────────────────────────────────────────────────────

def main() -> None:
    separator = "=" * 65

    print(separator)
    print("NEXUS-thirdy — Supabase Manual Setup")
    print(separator)
    print()
    print("IMPORTANT: The previous script used supabase.rpc('query', ...)")
    print("which is not a valid client method and silently failed.")
    print("Run the SQL blocks below manually in the Supabase SQL Editor.")
    print()
    print("Steps:")
    print("  1. Open https://supabase.com/dashboard")
    print("  2. Select your project")
    print("  3. Go to: SQL Editor → New Query")
    print("  4. Paste BLOCK 1 → click Run")
    print("  5. Clear the editor → paste BLOCK 2 → click Run")
    print()

    print("-" * 65)
    print("BLOCK 1 — Enable pgvector (paste this first):")
    print("-" * 65)
    print(PGVECTOR_SQL)

    print("-" * 65)
    print("BLOCK 2 — Create nexus_interactions table (paste this second):")
    print("-" * 65)
    print(INTERACTIONS_SQL)

    print(separator)
    print("After running both blocks, verify in the Supabase dashboard:")
    print("  • Table Editor     → 'nexus_interactions' should be listed")
    print("  • Database → Extensions → 'vector' should show as Enabled")
    print()
    print("Also set these environment variables on Render if not already done:")
    print("  SUPABASE_PROJECT_REF  = <your project reference ID>")
    print("  SUPABASE_POOLER_HOST  = <your connection pooler host>")
    print("  (Both found in: Supabase dashboard → Settings → Database)")
    print(separator)


if __name__ == "__main__":
    main()

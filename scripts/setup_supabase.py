"""
NEXUS-thirdy | scripts/setup_supabase.py
Phase 8 — One-time Supabase table setup

Run this ONCE to create the required tables in Supabase.
Tables created:
  - nexus_interactions: behavioral drift logging
  - nexus_memories: Mem0 vector store (auto-created by Mem0 on first use)

Usage:
  python scripts/setup_supabase.py
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from config.settings import settings


def setup_tables():
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        print("❌ SUPABASE_URL and SUPABASE_SERVICE_KEY not set in .env")
        return

    try:
        from supabase import create_client
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

        # Create interactions table for drift logging
        supabase.rpc("query", {"query": """
            CREATE TABLE IF NOT EXISTS nexus_interactions (
                id BIGSERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                skill TEXT NOT NULL,
                platform TEXT NOT NULL,
                success BOOLEAN DEFAULT TRUE,
                timestamp FLOAT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_interactions_user_id
                ON nexus_interactions(user_id);
            CREATE INDEX IF NOT EXISTS idx_interactions_skill
                ON nexus_interactions(skill);
            CREATE INDEX IF NOT EXISTS idx_interactions_timestamp
                ON nexus_interactions(timestamp);
        """}).execute()

        print("✅ nexus_interactions table created")
        print("\nDone. Supabase tables are ready.")

    except Exception as e:
        print(f"Note: {e}")
        print("\nAlternative: Run this SQL directly in Supabase SQL Editor:")
        print("""
CREATE TABLE IF NOT EXISTS nexus_interactions (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    platform TEXT NOT NULL,
    success BOOLEAN DEFAULT TRUE,
    timestamp FLOAT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
        """)


if __name__ == "__main__":
    setup_tables()

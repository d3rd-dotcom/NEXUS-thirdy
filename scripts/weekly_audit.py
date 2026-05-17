"""
NEXUS-thirdy | scripts/weekly_audit.py
Phase 10 — Weekly Self-Audit

Runs every week via GitHub Actions scheduled workflow.
Analyzes the last 7 days of interaction logs from Supabase.
Identifies:
  - Top performing skills (by volume and success rate)
  - Bottom performing skills (by error rate)
  - Most active users
  - Platform performance comparison
  - Groq rate limit frequency (signals need to upgrade)
  - Response time trends

Output: prints a report and optionally sends to a webhook.

Usage:
  python scripts/weekly_audit.py
  python scripts/weekly_audit.py --webhook https://your-webhook-url
"""

import sys
import os
import argparse
from datetime import datetime, timezone, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from config.settings import settings


def run_audit(webhook_url: str = ""):
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        print("❌ Supabase not configured. Set SUPABASE_URL and SUPABASE_SERVICE_KEY.")
        return

    try:
        from supabase import create_client
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    except Exception as e:
        print(f"❌ Supabase connection failed: {e}")
        return

    # Time range: last 7 days
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    week_ago_ts = week_ago.timestamp()

    print(f"\n{'='*60}")
    print(f"NEXUS-thirdy Weekly Audit")
    print(f"Period: {week_ago.strftime('%Y-%m-%d')} → {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    print(f"{'='*60}\n")

    try:
        # Fetch all interactions from last 7 days
        result = supabase.table("nexus_interactions") \
            .select("*") \
            .gte("timestamp", week_ago_ts) \
            .execute()

        interactions = result.data
        total = len(interactions)

        if total == 0:
            print("No interactions recorded this week.")
            return

        print(f"Total interactions: {total}")

        # ── Skill breakdown ───────────────────────────────────────────────────
        skill_counts = {}
        skill_errors = {}
        platform_counts = {}
        user_counts = {}

        for item in interactions:
            skill = item.get("skill", "unknown")
            platform = item.get("platform", "unknown")
            user_id = item.get("user_id", "unknown")
            success = item.get("success", True)

            skill_counts[skill] = skill_counts.get(skill, 0) + 1
            if not success:
                skill_errors[skill] = skill_errors.get(skill, 0) + 1

            platform_counts[platform] = platform_counts.get(platform, 0) + 1
            user_counts[user_id] = user_counts.get(user_id, 0) + 1

        # Top skills
        print(f"\n── Top Skills ──────────────────────────────")
        for skill, count in sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            error_count = skill_errors.get(skill, 0)
            error_rate = (error_count / count) * 100 if count > 0 else 0
            print(f"  {skill:<25} {count:>5} calls  {error_rate:.1f}% errors")

        # Bottom skills (by error rate, min 5 calls)
        print(f"\n── Skills Needing Attention ────────────────")
        problem_skills = [
            (skill, skill_errors.get(skill, 0) / count)
            for skill, count in skill_counts.items()
            if count >= 5 and skill_errors.get(skill, 0) > 0
        ]
        if problem_skills:
            for skill, error_rate in sorted(problem_skills, key=lambda x: x[1], reverse=True)[:3]:
                print(f"  {skill:<25} {error_rate*100:.1f}% error rate")
        else:
            print("  All skills performing well ✅")

        # Platform breakdown
        print(f"\n── Platform Breakdown ──────────────────────")
        for platform, count in sorted(platform_counts.items(), key=lambda x: x[1], reverse=True):
            pct = (count / total) * 100
            print(f"  {platform:<20} {count:>5} interactions ({pct:.1f}%)")

        # User stats
        unique_users = len(user_counts)
        returning_users = sum(1 for c in user_counts.values() if c > 1)
        print(f"\n── User Stats ──────────────────────────────")
        print(f"  Unique users:    {unique_users}")
        print(f"  Returning users: {returning_users}")
        print(f"  Avg per user:    {total/unique_users:.1f}")

        # Success rate
        total_errors = sum(skill_errors.values())
        success_rate = ((total - total_errors) / total) * 100
        print(f"\n── Overall Health ──────────────────────────")
        print(f"  Success rate:    {success_rate:.1f}%")
        print(f"  Total errors:    {total_errors}")

        print(f"\n{'='*60}")
        print("Audit complete.")
        print(f"{'='*60}\n")

        # Send to webhook if provided
        if webhook_url:
            import urllib.request
            import json
            report = {
                "week": week_ago.strftime("%Y-%m-%d"),
                "total_interactions": total,
                "unique_users": unique_users,
                "success_rate": round(success_rate, 2),
                "top_skill": max(skill_counts, key=skill_counts.get) if skill_counts else "none"
            }
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(report).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=10)
            print(f"Report sent to webhook: {webhook_url}")

    except Exception as e:
        print(f"❌ Audit failed: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NEXUS-thirdy weekly audit")
    parser.add_argument("--webhook", default="", help="Webhook URL to send report")
    args = parser.parse_args()
    run_audit(webhook_url=args.webhook)

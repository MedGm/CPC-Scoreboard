#!/usr/bin/env python3
"""
Blind Hour Reveal — Codeforces Data Fetcher
============================================
Fetches contest standings and submissions from the Codeforces API,
reconstructs the scoreboard at freeze time, identifies blind-hour
submissions, and exports everything the frontend needs as a single JSON.

Handles ICPC-style scoring (solved count + penalty with +20 min per WA),
team contests, large participant lists, and edge cases.

Usage:
    python fetch_contest.py <contest_id> [--freeze-minutes 60] [--output data.json]

Example:
    python fetch_contest.py 1234 --freeze-minutes 60 --output ../frontend/data/contest.json
"""

import argparse
import json
import sys
from pathlib import Path

# Fix path to import backend.core
sys.path.append(str(Path(__file__).parent.parent))
from backend import core


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Codeforces contest data for Blind Hour Reveal"
    )
    parser.add_argument("contest_id", type=int, help="Codeforces contest ID")
    parser.add_argument(
        "--freeze-minutes", type=int, default=60,
        help="Minutes into contest when scoreboard freezes (default: 60)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON path (default: ../frontend/data/contest_<id>.json)",
    )
    args = parser.parse_args()

    output_path = args.output
    if output_path is None:
        out_dir = Path(__file__).parent.parent / "frontend" / "data"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"contest_{args.contest_id}.json")

    try:
        # Use simple core logic
        data = core.build_reveal_data(args.contest_id, args.freeze_minutes)
    except Exception as exc:
        print(f"❌ Failed to fetch contest data: {exc}", file=sys.stderr)
        sys.exit(1)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    n_contestants = len(data["contestants"])
    n_blind = len(data["blindHourSubmissions"])
    accepted = sum(1 for s in data["blindHourSubmissions"] if s["verdict"] == "OK")
    print(f"\n✅ Exported {n_contestants} contestants, {n_blind} blind-hour subs ({accepted} AC)")
    print(f"📁 Saved to: {output_path}")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Dubai Strike Monitor — get_verification_queue.py
Returns a JSON array of events due for web verification.

Backoff schedule (seconds from event datetime):
  search_count=0 → due immediately (delta=0)
  search_count=1 → event_datetime + 3600   (1h)
  search_count=2 → event_datetime + 21600  (6h)
  search_count=3 → event_datetime + 86400  (24h)
  search_count=4 → event_datetime + 259200 (72h)
  search_count>=5 → _search_exhausted=true, skip
"""

import json
import sys
import os
from datetime import datetime, timezone

BACKOFF = [0, 3600, 21600, 86400, 259200]  # seconds from event datetime
MAX_CAP = 4
DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.json")


def parse_dt(s: str) -> datetime:
    """Parse ISO datetime string to UTC datetime."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def should_skip(event: dict, now: datetime) -> bool:
    """Return True if event should be skipped."""
    # Skip if confirmed, manual, or discarded
    if event.get("confirmed") is True:
        return True
    if event.get("_manual"):
        return True
    if event.get("_discard"):
        return True
    if event.get("_search_exhausted"):
        return True

    # Skip if event datetime > 7 days ago
    dt = parse_dt(event.get("datetime", ""))
    age_days = (now - dt).total_seconds() / 86400
    if age_days > 7:
        return True

    return False


def is_due(event: dict, now: datetime) -> bool:
    """Return True if this event is due for a verification search."""
    search_count = event.get("_search_count", 0)

    # Mark exhausted if count >= 5
    if search_count >= 5:
        return False

    next_search_at = event.get("_next_search_at")

    # Never searched → due immediately
    if next_search_at is None:
        return True

    # _next_search_at stored as Unix timestamp (0 = due immediately)
    if isinstance(next_search_at, (int, float)):
        return next_search_at <= now.timestamp()

    # Fallback: treat as not due
    return False


def main():
    now = datetime.now(timezone.utc)

    try:
        with open(DATA_FILE) as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading data.json: {e}", file=sys.stderr)
        print("[]")
        return

    events = data.get("events", [])
    due = []

    for event in events:
        if should_skip(event, now):
            continue
        if is_due(event, now):
            sources = [s.get("domain", "") for s in event.get("sources", [])]
            due.append({
                "id": event.get("id"),
                "title": event.get("title", ""),
                "location": event.get("location", "UAE"),
                "datetime": event.get("datetime", ""),
                "type": event.get("type", "security_alert"),
                "search_count": event.get("_search_count", 0),
                "sources": sources,
            })
            if len(due) >= MAX_CAP:
                break

    print(json.dumps(due, ensure_ascii=False))


if __name__ == "__main__":
    main()

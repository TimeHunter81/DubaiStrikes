#!/usr/bin/env python3
"""
Dubai Strike Monitor — apply_verifications.py
Applies web verification results to data.json with POSIX flock.

Usage:
  python3 apply_verifications.py --results '[{...}]'

Result format:
  [{
    "id": "...",
    "confirmed": true/false,
    "source_url": "...",
    "source_domain": "...",
    "lat": float_or_null,
    "lon": float_or_null,
    "precision": "exact|city|emirate|country",
    "location": "string_or_null"
  }]
"""

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(os.path.dirname(SCRIPTS_DIR), "data.json")
LOCK_FILE = DATA_FILE + ".lock"

sys.path.insert(0, SCRIPTS_DIR)
try:
    from pipeline_logger import log as _log
except ImportError:
    def _log(*a, **kw): pass

# Backoff schedule: seconds added to event datetime for next search
BACKOFF = [0, 3600, 21600, 86400, 259200]

# Precision ordering (higher index = more precise)
PRECISION_ORDER = {"country": 0, "emirate": 1, "city": 2, "exact": 3}


def parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def compute_next_search_at(event_dt: datetime, search_count: int) -> float:
    """Return Unix timestamp for the next search, based on backoff schedule."""
    if search_count < len(BACKOFF):
        delta = BACKOFF[search_count]
    else:
        # Beyond schedule → exhausted
        return float("inf")
    return event_dt.timestamp() + delta


def is_more_precise(new_precision: str, current_precision: str) -> bool:
    """Return True if new_precision is strictly more precise than current."""
    return PRECISION_ORDER.get(new_precision, -1) > PRECISION_ORDER.get(current_precision, -1)


def apply_result(event: dict, result: dict) -> None:
    """Apply a single verification result to an event (in-place)."""
    # Increment search count
    count = event.get("_search_count", 0) + 1
    event["_search_count"] = count

    # Compute next search timestamp
    event_dt = parse_dt(event.get("datetime", ""))
    if count >= 5:
        event["_search_exhausted"] = True
        event["_next_search_at"] = None
    else:
        event["_next_search_at"] = compute_next_search_at(event_dt, count)

    # Apply confirmation
    if result.get("confirmed") is True:
        event["confirmed"] = True

        # Add source if not already present
        source_domain = result.get("source_domain", "")
        source_url = result.get("source_url", "")
        existing_domains = {s.get("domain", "") for s in event.get("sources", [])}
        if source_domain and source_domain not in existing_domains and source_url:
            event.setdefault("sources", []).append({
                "url": source_url,
                "domain": source_domain,
                "tier": "wire",  # default; caller can be more specific
            })

    # Update location if more precise
    new_precision = result.get("precision", "")
    current_precision = event.get("precision", "country")
    if new_precision and is_more_precise(new_precision, current_precision):
        new_lat = result.get("lat")
        new_lon = result.get("lon")
        new_loc = result.get("location")
        if new_lat is not None and new_lon is not None:
            event["lat"] = float(new_lat)
            event["lon"] = float(new_lon)
            event["precision"] = new_precision
            if new_loc:
                event["location"] = new_loc


def main():
    parser = argparse.ArgumentParser(description="Apply verification results to data.json")
    parser.add_argument("--results", required=True, help="JSON array of verification results")
    args = parser.parse_args()

    try:
        results = json.loads(args.results)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON in --results: {e}", file=sys.stderr)
        sys.exit(1)

    if not results:
        print("[apply_verifications] No results to apply.")
        _log("verify", "run_done", applied=0, confirmed=0, location_upgraded=0)
        return

    # Acquire exclusive flock
    lock_f = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX)

        # Load data
        with open(DATA_FILE) as f:
            data = json.load(f)

        events_by_id = {e["id"]: e for e in data.get("events", [])}
        applied = 0
        confirmed = 0
        location_upgraded = 0

        for result in results:
            eid = result.get("id")
            if not eid or eid not in events_by_id:
                print(f"[WARN] Event not found: {eid}", file=sys.stderr)
                continue

            event = events_by_id[eid]
            old_precision = event.get("precision", "country")
            was_confirmed = event.get("confirmed", False)

            apply_result(event, result)
            applied += 1

            if result.get("confirmed") is True:
                confirmed += 1
                if not was_confirmed:
                    _log("verify", "event_confirmed",
                         id=eid,
                         title=event.get("title", "")[:80],
                         via=result.get("source_domain", ""),
                         search_count=event.get("_search_count", 0))

            if is_more_precise(result.get("precision", ""), old_precision):
                location_upgraded += 1
                _log("verify", "location_upgraded",
                     id=eid,
                     title=event.get("title", "")[:80],
                     old_precision=old_precision,
                     new_precision=result.get("precision", ""),
                     location=result.get("location", ""))

            if event.get("_search_exhausted"):
                _log("verify", "search_exhausted",
                     id=eid,
                     title=event.get("title", "")[:80],
                     confirmed=event.get("confirmed", False))

        # Save
        data["events"] = list(events_by_id.values())
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"[apply_verifications] Applied {applied} results | {confirmed} confirmed | {location_upgraded} location-upgraded")
        _log("verify", "run_done", applied=applied, confirmed=confirmed, location_upgraded=location_upgraded)

    finally:
        fcntl.flock(lock_f, fcntl.LOCK_UN)
        lock_f.close()


if __name__ == "__main__":
    main()

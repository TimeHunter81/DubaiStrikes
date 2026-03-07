#!/usr/bin/env python3
"""
Dubai Strike Monitor — fetch_events.py
Fetches security events from GDELT, clusters, deduplicates, and updates data.json.
"""

import json
import os
import sys
import time
import hashlib
import re
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote_plus
from urllib.error import URLError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(REPO_ROOT, "data.json")

# GDELT Full Text Search API
GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Search queries for Dubai/UAE security events
QUERIES = [
    "Dubai strike attack missile drone",
    "UAE security incident attack",
    "Dubai explosion attack threat",
    "Abu Dhabi strike missile",
    "UAE Houthi attack strike",
]

# Source classification
SOURCE_TIERS = {
    "official": ["ncema.gov.ae", "wam.ae", "dubaipolice.gov.ae", "mofaic.gov.ae", "uaemfa.com"],
    "wire": ["reuters.com", "apnews.com", "afp.com", "bloomberg.com", "bbc.com", "bbc.co.uk"],
    "local": ["gulfnews.com", "thenationalnews.com", "khaleejtimes.com", "arabnews.com", "alarabiya.net"],
    "social": ["twitter.com", "x.com", "t.me", "telegram.me"],
}

# Dubai/UAE bounding box (approx)
UAE_BBOX = {"lat_min": 22.5, "lat_max": 26.5, "lon_min": 51.0, "lon_max": 56.5}

# Default coordinates for known locations
LOCATION_COORDS = {
    "dubai": (25.2048, 55.2708),
    "abu dhabi": (24.4539, 54.3773),
    "sharjah": (25.3463, 55.4209),
    "ajman": (25.4052, 55.5136),
    "fujairah": (25.1288, 56.3265),
    "ras al khaimah": (25.7895, 55.9432),
    "uae": (24.5, 54.5),
    "united arab emirates": (24.5, 54.5),
}


def get_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url[:30]


def classify_source(domain: str) -> str:
    for tier, domains in SOURCE_TIERS.items():
        if any(d in domain for d in domains):
            return tier
    return "unverified"


def fetch_gdelt(query: str, max_records: int = 50) -> list:
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": str(max_records),
        "format": "json",
        "timespan": "7d",
        "sort": "datedesc",
    }
    url = f"{GDELT_API}?{urlencode(params)}"
    try:
        req = Request(url, headers={"User-Agent": "DubaiStrikeMonitor/1.0"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("articles", [])
    except Exception as e:
        print(f"[GDELT] Error for query '{query}': {e}", file=sys.stderr)
        return []


def parse_date(datestr: str) -> str:
    """Parse GDELT date format: YYYYMMDDHHMMSS → YYYY-MM-DD"""
    if not datestr or len(datestr) < 8:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        return f"{datestr[:4]}-{datestr[4:6]}-{datestr[6:8]}"
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def extract_location(title: str, url: str) -> tuple:
    """Guess location from title text, return (location_name, lat, lon)."""
    title_lower = title.lower()
    for loc, coords in LOCATION_COORDS.items():
        if loc in title_lower:
            return loc.title(), coords[0], coords[1]
    # Default to Dubai
    return "UAE", 25.2048, 55.2708


def compute_event_id(title: str, date: str) -> str:
    """Stable ID based on title + date."""
    h = hashlib.sha1(f"{title}:{date}".encode()).hexdigest()
    return h[:12]


def compute_confidence(sources: list) -> str:
    """Compute confidence level based on source tiers."""
    tiers = [s["tier"] for s in sources]
    if "official" in tiers or (tiers.count("wire") >= 1 and len(sources) >= 2):
        return "high"
    if "wire" in tiers or tiers.count("local") >= 2:
        return "medium"
    if len(sources) >= 1 and "social" not in tiers[0:1]:
        return "low"
    return "unverified"


def cluster_articles(articles: list) -> list:
    """Cluster articles into events by title similarity + date."""
    events = {}
    for art in articles:
        title = art.get("title", "").strip()
        url = art.get("url", "")
        date = parse_date(art.get("seendate", ""))
        if not title or not url:
            continue

        domain = get_domain(url)
        tier = classify_source(domain)
        eid = compute_event_id(title, date)

        if eid not in events:
            loc_name, lat, lon = extract_location(title, url)
            events[eid] = {
                "id": eid,
                "title": title,
                "date": date,
                "location": loc_name,
                "lat": lat,
                "lon": lon,
                "sources": [],
                "confidence": "unverified",
            }

        src = {"url": url, "domain": domain, "tier": tier}
        if src not in events[eid]["sources"]:
            events[eid]["sources"].append(src)

    # Compute confidence + deduplicate sources
    result = []
    for e in events.values():
        seen_domains = set()
        deduped_sources = []
        for s in e["sources"]:
            if s["domain"] not in seen_domains:
                deduped_sources.append(s)
                seen_domains.add(s["domain"])
        e["sources"] = deduped_sources[:5]  # max 5 sources per event
        e["confidence"] = compute_confidence(e["sources"])
        result.append(e)

    return sorted(result, key=lambda x: x["date"], reverse=True)


def merge_with_existing(new_events: list, existing_events: list, max_age_days: int = 30) -> list:
    """Merge new events with existing, deduplicate by id, drop old events."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).strftime("%Y-%m-%d")

    existing_by_id = {e["id"]: e for e in existing_events if e.get("date", "") >= cutoff}

    for e in new_events:
        eid = e["id"]
        if eid in existing_by_id:
            # Merge sources
            existing_sources = existing_by_id[eid]["sources"]
            existing_domains = {s["domain"] for s in existing_sources}
            for s in e["sources"]:
                if s["domain"] not in existing_domains:
                    existing_sources.append(s)
                    existing_domains.add(s["domain"])
            existing_by_id[eid]["sources"] = existing_sources[:5]
            existing_by_id[eid]["confidence"] = compute_confidence(existing_by_id[eid]["sources"])
        else:
            existing_by_id[eid] = e

    return sorted(existing_by_id.values(), key=lambda x: x["date"], reverse=True)[:100]


def compute_status(events: list) -> str:
    if not events:
        return "clear"
    confs = [e["confidence"] for e in events[:10]]  # look at 10 most recent
    if "high" in confs:
        return "alert"
    if "medium" in confs:
        return "warning"
    return "clear"


def load_existing() -> dict:
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_updated": "", "status": "clear", "events": []}


def save(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[OK] Saved {len(data['events'])} events → {DATA_FILE}")


def main():
    print(f"[START] {datetime.now(timezone.utc).isoformat()}")

    existing = load_existing()
    all_articles = []

    for query in QUERIES:
        print(f"[GDELT] Fetching: {query}")
        articles = fetch_gdelt(query)
        print(f"  → {len(articles)} articles")
        all_articles.extend(articles)
        time.sleep(1.2)  # soft rate limit

    new_events = cluster_articles(all_articles)
    print(f"[CLUSTER] {len(new_events)} clustered events from {len(all_articles)} articles")

    merged = merge_with_existing(new_events, existing.get("events", []))

    data = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "status": compute_status(merged),
        "events": merged,
    }

    save(data)
    print(f"[DONE] Status: {data['status']}")


if __name__ == "__main__":
    main()

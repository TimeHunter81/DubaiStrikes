#!/usr/bin/env python3
"""
Dubai Strike Monitor — fetch_events.py
Sources:
  1. RSS feeds           — near real-time (5-15 min), no rate limits
  2. GDELT Full Text API — broad coverage, 15-30 min delay
  3. NOTAM scraping      — airspace alerts (early warning, pre-media)
  4. LLM location boost  — Claude Haiku via ANTHROPIC_API_KEY (optional)
"""

import json
import os
import sys
import time
import hashlib
import re
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError, HTTPError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(REPO_ROOT, "data.json")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── RSS ─────────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # Wire / international
    {"url": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",    "tier": "wire",     "name": "BBC Middle East"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",                   "tier": "wire",     "name": "Al Jazeera"},
    {"url": "https://www.theguardian.com/world/middleeast/rss",            "tier": "wire",     "name": "The Guardian ME"},
    # Local / regional
    {"url": "https://www.thenationalnews.com/arc/outboundfeeds/rss/?outputType=xml", "tier": "local", "name": "The National"},
    {"url": "https://www.middleeasteye.net/rss",                           "tier": "local",    "name": "Middle East Eye"},
    {"url": "https://meduza.io/rss2/en/all",                               "tier": "local",    "name": "Meduza"},
    {"url": "https://gulfnews.com/stories.rss",                            "tier": "local",    "name": "Gulf News"},
]

# Patterns that indicate analysis/opinion — not real events
NOISE_PATTERNS = [
    r"^(\d+|one|two|three|four|five|six|seven|eight|nine|ten) (day|week|month|hour)s? (on|later|after)",  # "Seven days on"
    r"^\d+ (question|thing|reason|fact|way|key|point)",  # "5 questions about"
    r"\b(analysis|opinion|explainer|explained|what (we know|to know)|in depth|in-depth|backgrounder|comment|review|weekly|roundup|wrap.?up|digest|briefing)\b",
    r"\?$",           # title ends with question mark
    r"^(why|how|what|who|when|could|should|would|will|is|are|can) ",  # question headlines
    r"\b(history|historical|context|background|profile|portrait|timeline)\b",
]

# UAE/Gulf keywords to filter RSS items
RSS_KEYWORDS = [
    "dubai", "abu dhabi", "uae", "emirates", "sharjah", "gulf",
    "iran", "irgc", "houthi", "hezbollah", "missile", "drone",
    "strike", "attack", "explosion", "airspace", "military", "interception",
]

# ── GDELT ───────────────────────────────────────────────────────────────────
# Only keep GDELT articles from these domains (sources with no free RSS feed)
# BBC/AJ/Guardian already covered via RSS — avoid double-counting
GDELT_TRUSTED_DOMAINS = {
    "reuters.com",    # wire #1 — no free RSS
    "apnews.com",     # wire #2 — no free RSS
    "afp.com",        # wire #3 — French angle (Camp de la Paix)
    "cnn.com",        # verified video, no ME RSS
    "nytimes.com",    # diplomatic/intelligence sources
    "iranintl.com",   # Iran International — fast on IRGC claims
}

GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"
QUERIES = [
    # UAE direct
    "Dubai strike attack missile drone",
    "UAE security incident attack explosion",
    "Abu Dhabi strike missile attack",
    # Actors
    "Iran IRGC UAE attack strike missile",
    "Hezbollah UAE strike attack Gulf",
    "Syria missile strike Gulf Emirates",
    # Regional escalation
    "Iran attack Gulf states retaliation",
    "Houthi UAE strike missile drone",
]

# ── NOTAM ───────────────────────────────────────────────────────────────────
# UAE airports to monitor (ICAO codes)
UAE_AIRPORTS = {
    "OMDB": {"name": "Dubai International", "lat": 25.2528, "lon": 55.3644},
    "OMAA": {"name": "Abu Dhabi International", "lat": 24.4330, "lon": 54.6511},
    "OMSJ": {"name": "Sharjah International", "lat": 25.3286, "lon": 55.5172},
    "OMRK": {"name": "Ras Al Khaimah International", "lat": 25.6135, "lon": 55.9388},
    "OMFJ": {"name": "Fujairah International", "lat": 25.1122, "lon": 56.3240},
}

# Q-codes indicating restricted/danger area — relevant to military activity
ALERT_QCODES = {"QRTCA", "QRDCA", "QRPCA", "QRCCA", "QLCAS", "QWWWS"}

# Keywords in NOTAM text that suggest military/security events
ALERT_KEYWORDS = ["military", "restricted", "danger area", "prohibited", "missile",
                  "drone", "armed", "weapon", "security", "conflict", "hostile"]

# ── SOURCE CLASSIFICATION ───────────────────────────────────────────────────
SOURCE_TIERS = {
    "official": ["ncema.gov.ae", "wam.ae", "dubaipolice.gov.ae", "mofaic.gov.ae",
                 "gcaa.gov.ae", "uaemfa.com", "mofa.gov.ae"],
    "wire":     ["reuters.com", "apnews.com", "afp.com", "bloomberg.com",
                 "bbc.com", "bbc.co.uk", "theguardian.com"],
    "local":    ["gulfnews.com", "thenationalnews.com", "khaleejtimes.com",
                 "arabnews.com", "alarabiya.net", "aljazeera.com"],
    "osint":    ["bellingcat.com", "osintdefender.com", "liveuamap.com"],
    "social":   ["twitter.com", "x.com", "t.me", "telegram.me"],
}

# Known locations → (lat, lon)
LOCATION_COORDS = {
    "dubai international airport": (25.2528, 55.3644),
    "al dhafra air base":          (24.2481, 54.5478),
    "abu dhabi international":     (24.4330, 54.6511),
    "dubai":                       (25.2048, 55.2708),
    "abu dhabi":                   (24.4539, 54.3773),
    "sharjah":                     (25.3463, 55.4209),
    "ajman":                       (25.4052, 55.5136),
    "fujairah":                    (25.1288, 56.3265),
    "ras al khaimah":              (25.7895, 55.9432),
    "umm al quwain":               (25.5647, 55.5553),
    "uae":                         (24.5, 54.5),
    "united arab emirates":        (24.5, 54.5),
}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def http_get(url: str, timeout: int = 15, retries: int = 3, skip_on_429: bool = False) -> bytes:
    """GET with retry + exponential backoff."""
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "DubaiStrikeMonitor/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except HTTPError as e:
            if e.code == 429:
                if skip_on_429:
                    raise  # caller handles it, no wait
                wait = 30 * (2 ** attempt)
                print(f"  [rate-limit] 429, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
            else:
                raise
        except URLError as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                raise
    return b""


def get_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url[:30]


def classify_source(domain: str) -> str:
    for tier, domains in SOURCE_TIERS.items():
        if any(d in domain for d in domains):
            return tier
    return "unverified"


def compute_event_id(title: str, date: str) -> str:
    h = hashlib.sha1(f"{title}:{date}".encode()).hexdigest()
    return h[:12]


def compute_confidence(sources: list) -> str:
    tiers = [s["tier"] for s in sources]
    if "official" in tiers or (tiers.count("wire") >= 1 and len(sources) >= 2):
        return "high"
    if "wire" in tiers or tiers.count("local") >= 2:
        return "medium"
    if len(sources) >= 1 and "social" not in tiers[:1]:
        return "low"
    return "unverified"


# ── RSS ─────────────────────────────────────────────────────────────────────
def parse_rss_date(datestr: str) -> str:
    """Parse RSS pubDate (RFC 2822) → ISO 8601 UTC."""
    if not datestr:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT",
                "%a, %d %b %Y %H:%M:%S +0000"):
        try:
            dt = datetime.strptime(datestr.strip(), fmt)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_rss(feed: dict) -> list:
    """Fetch and parse an RSS feed. Returns list of article dicts."""
    try:
        raw = http_get(feed["url"], timeout=10).decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[RSS] {feed['name']}: {e}", file=sys.stderr)
        return []

    items = re.findall(r"<item>(.*?)</item>", raw, re.DOTALL)
    results = []
    for item in items:
        title_m   = re.search(r"<title[^>]*><!\[CDATA\[(.*?)\]\]>|<title[^>]*>(.*?)</title>", item, re.DOTALL)
        link_m    = re.search(r"<link[^>]*>(https?://[^<]+)</link>|<link[^>]*/?>.*?href=\"(https?://[^\"]+)\"", item, re.DOTALL)
        date_m    = re.search(r"<pubDate[^>]*>(.*?)</pubDate>", item, re.DOTALL)
        desc_m    = re.search(r"<description[^>]*><!\[CDATA\[(.*?)\]\]>|<description[^>]*>(.*?)</description>", item, re.DOTALL)

        title = (title_m.group(1) or title_m.group(2) or "").strip() if title_m else ""
        url   = (link_m.group(1) or link_m.group(2) or "").strip() if link_m else ""
        date  = parse_rss_date(date_m.group(1).strip() if date_m else "")
        desc  = re.sub(r"<[^>]+>", " ", (desc_m.group(1) or desc_m.group(2) or "") if desc_m else "").strip()[:300]

        if not title or not url:
            continue

        # Filter: must contain at least one UAE/Gulf keyword
        combined = (title + " " + desc).lower()
        if not any(kw in combined for kw in RSS_KEYWORDS):
            continue



        # Skip articles older than 7 days
        try:
            art_dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - art_dt).days > 7:
                continue
        except Exception:
            pass

        results.append({
            "title": title,
            "url": url,
            "datetime": date,
            "description": desc,
            "domain": get_domain(url),
            "tier": feed["tier"],
            "source_name": feed["name"],
        })

    print(f"[RSS] {feed['name']}: {len(results)} relevant items")
    return results


def rss_articles_to_events(articles: list) -> list:
    """Cluster RSS articles into events (same logic as GDELT)."""
    events = {}
    for art in articles:
        title    = art["title"]
        date_day = art["datetime"][:10]
        eid      = compute_event_id(title, date_day)

        if eid not in events:
            loc_name, lat, lon, precision = extract_location(title, art.get("description", ""))
            events[eid] = {
                "id":        eid,
                "datetime":  art["datetime"],
                "type":      "security_alert",
                "confirmed": art["tier"] in ("wire", "official"),
                "precision": precision,
                "title":     title,
                "location":  loc_name,
                "lat":       lat if precision != "country" else None,
                "lon":       lon if precision != "country" else None,
                "sources":   [],
            }

        src = {"url": art["url"], "domain": art["domain"], "tier": art["tier"]}
        if src not in events[eid]["sources"]:
            events[eid]["sources"].append(src)

    result = []
    for e in events.values():
        e["sources"] = e["sources"][:5]
        e["confidence"] = compute_confidence(e["sources"])
        result.append(e)
    return sorted(result, key=lambda x: x["datetime"], reverse=True)


# ── LOCATION EXTRACTION ─────────────────────────────────────────────────────
def extract_location_basic(text: str) -> tuple:
    """Keyword-based location extraction. Returns (name, lat, lon, precision)."""
    text_lower = text.lower()
    # Try longest match first (most specific)
    for loc in sorted(LOCATION_COORDS, key=len, reverse=True):
        if loc in text_lower:
            lat, lon = LOCATION_COORDS[loc]
            precision = "exact" if len(loc) > 8 and "airport" in loc or "base" in loc else "city"
            return loc.title(), lat, lon, precision
    return "UAE", 24.5, 54.5, "country"


def extract_location_llm(title: str, description: str = "") -> tuple | None:
    """
    Claude Haiku extracts the most precise location from the article title.
    Returns (location_str, lat, lon, precision) or None.
    """
    if not ANTHROPIC_API_KEY:
        return None

    prompt = (
        "Extract the most precise location from this UAE security news headline. "
        "Return ONLY JSON, no explanation:\n"
        f"Headline: {title[:200]}\n\n"
        '{"location":"<most specific place name>","lat":<float or null>,"lon":<float or null>,"precision":"exact|city|emirate|country"}'
    )
    try:
        import json as _json
        payload = _json.dumps({
            "model": "claude-haiku-4-5",
            "max_tokens": 120,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST"
        )
        with urlopen(req, timeout=12) as resp:
            data = _json.loads(resp.read())
            text = data["content"][0]["text"].strip()
            text = re.sub(r"```(?:json)?", "", text).strip()
            r = _json.loads(text)
            loc = r.get("location", "")
            lat, lon = r.get("lat"), r.get("lon")
            prec = r.get("precision", "country")
            if loc and lat and lon:
                return loc, float(lat), float(lon), prec
            if loc:
                basic = extract_location_basic(loc)
                return loc, basic[1], basic[2], prec
    except Exception as e:
        print(f"  [LLM] {e}", file=sys.stderr)
    return None


def extract_location(title: str, description: str = "") -> tuple:
    """Try LLM first, fall back to keyword matching."""
    llm = extract_location_llm(title, description)
    if llm:
        print(f"  [LLM] '{llm[0]}' ({llm[3]}) lat={llm[1]:.4f} lon={llm[2]:.4f}")
        return llm
    return extract_location_basic(title)


# ── GDELT ───────────────────────────────────────────────────────────────────
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
        # timeout=8s + skip_on_429: no wait/retry — rate-limit backoff kills the cron timeout
        data = json.loads(http_get(url, timeout=8, skip_on_429=True))
        return data.get("articles", [])
    except HTTPError as e:
        if e.code == 429:
            print(f"[GDELT] Rate-limited for '{query}' — skipping", file=sys.stderr)
        else:
            print(f"[GDELT] HTTP {e.code} for '{query}'", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[GDELT] Error for '{query}': {e}", file=sys.stderr)
        return []


def parse_gdelt_date(datestr: str) -> str:
    if not datestr or len(datestr) < 8:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        dt = datetime(int(datestr[:4]), int(datestr[4:6]), int(datestr[6:8]),
                      int(datestr[8:10]) if len(datestr) > 9 else 0,
                      int(datestr[10:12]) if len(datestr) > 11 else 0,
                      tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cluster_articles(articles: list) -> list:
    events = {}
    for art in articles:
        title = art.get("title", "").strip()
        url = art.get("url", "")
        if not title or not url:
            continue
        date_str = parse_gdelt_date(art.get("seendate", ""))
        date_day = date_str[:10]

        domain = get_domain(url)

        # Filter: only keep articles from trusted domains
        if not any(td in domain for td in GDELT_TRUSTED_DOMAINS):
            continue

        tier = classify_source(domain)
        eid = compute_event_id(title, date_day)

        if eid not in events:
            loc_name, lat, lon, precision = extract_location(title, art.get("socialimage", ""))
            events[eid] = {
                "id": eid,
                "datetime": date_str,
                "type": "security_alert",   # GDELT default; can be refined
                "confirmed": False,
                "precision": precision,
                "title": title,
                "location": loc_name,
                "lat": lat if precision != "country" else None,
                "lon": lon if precision != "country" else None,
                "sources": [],
                "confidence": "unverified",
            }

        src = {"url": url, "domain": domain, "tier": tier}
        if src not in events[eid]["sources"]:
            events[eid]["sources"].append(src)

    result = []
    for e in events.values():
        seen = set()
        deduped = []
        for s in e["sources"]:
            if s["domain"] not in seen:
                deduped.append(s)
                seen.add(s["domain"])
        e["sources"] = deduped[:5]
        e["confidence"] = compute_confidence(e["sources"])
        if e["confidence"] in ("high", "medium"):
            e["confirmed"] = True
        result.append(e)

    return sorted(result, key=lambda x: x["datetime"], reverse=True)


# ── NOTAM ───────────────────────────────────────────────────────────────────
def fetch_notams(icao: str) -> list:
    """Scrape notamify.com for a given ICAO code. Returns list of NOTAM dicts."""
    url = f"https://www.notamify.com/notams/{icao}"
    try:
        html = http_get(url, timeout=8).decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[NOTAM] Failed to fetch {icao}: {e}", file=sys.stderr)
        return []

    # notamify embeds NOTAM data as JSON in <script> tags
    # Look for arrays of notam objects
    matches = re.findall(r'\{[^{}]*"notam_number"\s*:\s*"([^"]+)"[^{}]*"message"\s*:\s*"([^"]+)"[^{}]*\}', html)
    results = []

    # Also try to find Q-codes
    notam_blocks = re.findall(
        r'"notam_number"\s*:\s*"([^"]+)".*?"message"\s*:\s*"([^"]*)".*?"icao_message"\s*:\s*"([^"]*)"',
        html, re.DOTALL
    )

    # Simpler approach: find all JSON-like objects with notam_number
    raw_json_matches = re.findall(r'\{[^<]{100,2000}?"notam_number"[^<]{50,2000}?\}', html)

    for match in raw_json_matches[:20]:
        try:
            # Clean escape sequences
            clean = match.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
            notam_id_m = re.search(r'"notam_number"\s*:\s*"([^"]+)"', match)
            msg_m = re.search(r'"message"\s*:\s*"([^"]+)"', match)
            icao_msg_m = re.search(r'"icao_message"\s*:\s*"([^"]+)"', match)
            starts_m = re.search(r'"starts_at"\s*:\s*"([^"]+)"', match)

            if not notam_id_m:
                continue
            notam_id = notam_id_m.group(1)
            message = msg_m.group(1) if msg_m else ""
            icao_msg = icao_msg_m.group(1).replace('\\n', '\n') if icao_msg_m else ""
            starts_at = starts_m.group(1) if starts_m else ""

            # Extract Q-code from icao_message: Q) OMAE/QRTCA/...
            qcode_m = re.search(r'Q\)\s*\w+/(\w+)/', icao_msg)
            qcode = qcode_m.group(1) if qcode_m else ""

            results.append({
                "notam_id": notam_id,
                "icao": icao,
                "qcode": qcode,
                "message": message.replace('\\n', '\n'),
                "starts_at": starts_at,
            })
        except Exception:
            pass

    return results


def notam_is_security_relevant(notam: dict) -> bool:
    """Returns True if this NOTAM suggests a security/military event."""
    if notam.get("qcode") in ALERT_QCODES:
        return True
    msg_lower = (notam.get("message", "") + " " + notam.get("icao", "")).lower()
    return any(kw in msg_lower for kw in ALERT_KEYWORDS)


def notams_to_events(airport_icao: str) -> list:
    """Convert security-relevant NOTAMs into event dicts."""
    airport = UAE_AIRPORTS.get(airport_icao, {})
    all_notams = fetch_notams(airport_icao)
    print(f"[NOTAM] {airport_icao}: {len(all_notams)} NOTAMs, checking for security alerts…")

    events = []
    for n in all_notams:
        if not notam_is_security_relevant(n):
            continue

        notam_id = n["notam_id"]
        eid = f"notam_{hashlib.sha1(notam_id.encode()).hexdigest()[:10]}"
        starts_at = n.get("starts_at", datetime.now(timezone.utc).isoformat())
        # Normalise to Z suffix
        if starts_at and not starts_at.endswith("Z"):
            starts_at = starts_at.replace("+00:00", "") + "Z"

        title = f"NOTAM {notam_id} — airspace restriction at {airport.get('name', airport_icao)}"
        if n.get("message"):
            title = f"NOTAM {notam_id}: {n['message'][:120]}"

        event = {
            "id": eid,
            "datetime": starts_at,
            "type": "security_alert",
            "confirmed": True,      # NOTAMs are official
            "precision": "exact",
            "title": title,
            "location": airport.get("name", airport_icao),
            "lat": airport.get("lat"),
            "lon": airport.get("lon"),
            "sources": [{"url": f"https://www.notamify.com/notams/{airport_icao}",
                         "domain": "notamify.com", "tier": "official"}],
            "confidence": "high",
            "_notam_id": notam_id,
            "_qcode": n.get("qcode", ""),
        }
        events.append(event)
        print(f"  ⚠️  Security NOTAM: {notam_id} ({n.get('qcode','?')}) @ {airport_icao}")

    return events


# ── MERGE ───────────────────────────────────────────────────────────────────
def merge_with_existing(new_events: list, existing_events: list, max_age_days: int = 30) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()[:10]

    existing_by_id = {
        e["id"]: e for e in existing_events
        if (e.get("datetime") or e.get("date", ""))[:10] >= cutoff
           or e.get("_manual")  # keep hand-curated events forever
    }

    for e in new_events:
        eid = e["id"]
        if eid in existing_by_id:
            ex = existing_by_id[eid]
            # Merge sources
            existing_domains = {s["domain"] for s in ex.get("sources", [])}
            for s in e.get("sources", []):
                if s["domain"] not in existing_domains:
                    ex.setdefault("sources", []).append(s)
                    existing_domains.add(s["domain"])
            ex["sources"] = ex.get("sources", [])[:5]
            ex["confidence"] = compute_confidence(ex["sources"])
        else:
            existing_by_id[eid] = e

    all_sorted = sorted(existing_by_id.values(),
                        key=lambda x: (x.get("datetime") or x.get("date", ""))[:19],
                        reverse=True)

    # Always keep _manual events; cap only auto-fetched ones at 100
    manual = [e for e in all_sorted if e.get("_manual")]
    auto   = [e for e in all_sorted if not e.get("_manual")][:100]
    return sorted(manual + auto,
                  key=lambda x: (x.get("datetime") or x.get("date", ""))[:19],
                  reverse=True)


# ── MAIN ────────────────────────────────────────────────────────────────────
def load_existing() -> dict:
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_updated": "", "events": []}


def save(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[OK] Saved {len(data['events'])} events → {DATA_FILE}")


def main():
    print(f"[START] {datetime.now(timezone.utc).isoformat()}")
    llm_status = "Claude Haiku" if ANTHROPIC_API_KEY else "disabled (set ANTHROPIC_API_KEY)"
    print(f"[LLM] {llm_status}")

    existing = load_existing()
    all_new_events = []

    # 1. RSS — every run (fast, no rate limits)
    for feed in RSS_FEEDS:
        articles = fetch_rss(feed)
        rss_events = rss_articles_to_events(articles)
        all_new_events.extend(rss_events)
        time.sleep(1)

    # ── Decide whether to run GDELT this turn ───────────────────────────────
    # GDELT indexes articles with 15-30 min delay → running every 10 min is redundant.
    # We track last GDELT run in .fetch_state.json and skip if < 18 min ago.
    STATE_FILE = os.path.join(os.path.dirname(DATA_FILE), ".fetch_state.json")
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}

    now_ts = time.time()
    last_gdelt = state.get("last_gdelt_run", 0)
    run_gdelt = (now_ts - last_gdelt) >= 18 * 60

    # 1. GDELT (every ~20 min)
    if run_gdelt:
        all_articles = []
        for query in QUERIES:
            print(f"[GDELT] {query!r}")
            arts = fetch_gdelt(query)
            print(f"  → {len(arts)} articles")
            all_articles.extend(arts)
            time.sleep(1)  # GDELT allows ~1req/s burst
        gdelt_events = cluster_articles(all_articles)
        print(f"[GDELT] → {len(gdelt_events)} events from {len(all_articles)} articles")
        all_new_events.extend(gdelt_events)
        state["last_gdelt_run"] = now_ts
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    else:
        print(f"[GDELT] skip ({(now_ts - last_gdelt)/60:.0f}m ago, threshold 18m)")

    # 3. NOTAMs — every run, they're real-time
    for icao in UAE_AIRPORTS:
        notam_events = notams_to_events(icao)
        all_new_events.extend(notam_events)
        time.sleep(2)

    # 3. Merge
    merged = merge_with_existing(all_new_events, existing.get("events", []))

    data = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "conflict_start": existing.get("conflict_start", "2026-02-27T20:00:00Z"),
        "events": merged,
    }

    save(data)
    security_notams = sum(1 for e in merged if e.get("_qcode"))
    print(f"[DONE] {len(merged)} total events | {security_notams} NOTAM alerts")


if __name__ == "__main__":
    main()

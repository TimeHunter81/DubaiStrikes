# Dubai Strike Monitor

Public aggregator of security events (strikes, attacks, threats) in Dubai & the UAE.
Cross-references official sources, wire services, and regional media with confidence scoring.

**Live:** https://timehunter81.github.io/DubaiStrikes/

## Architecture

```
OpenClaw Cron (every 12h)
    → agent/fetch_events.py
    → GDELT Full Text API + optional web_search validation
    → cluster, deduplicate, confidence score
    → commit data.json + push to GitHub
GitHub Pages → static frontend (index.html reads data.json)
```

No backend. No server to maintain. Agent does all the heavy lifting.

## Confidence Levels

| Level | Criteria |
|-------|----------|
| 🟢 High | Official UAE source (NCEMA, WAM, Dubai Police) OR wire + 2+ sources |
| 🟡 Medium | Wire service alone OR 2+ regional sources |
| 🔴 Low | Single unverified source |
| ⬜ Unverified | Social media only |

## Source Tiers

- **official:** ncema.gov.ae, wam.ae, dubaipolice.gov.ae, mofaic.gov.ae
- **wire:** reuters.com, apnews.com, bbc.com, bloomberg.com, afp.com
- **local:** gulfnews.com, thenationalnews.com, khaleejtimes.com, arabnews.com
- **social:** twitter/X, Telegram

## Manual Event Addition

Edit `data.json` directly and push. Format:

```json
{
  "id": "abc123def456",
  "title": "Description of the event",
  "date": "2026-03-07",
  "location": "Dubai",
  "lat": 25.2048,
  "lon": 55.2708,
  "confidence": "high",
  "sources": [
    { "url": "https://wam.ae/...", "domain": "wam.ae", "tier": "official" }
  ]
}
```

## Setup

1. Clone the repo
2. Add deploy key (repo Settings → Deploy keys → Allow write access)
3. Enable GitHub Pages (Settings → Pages → Source: GitHub Actions)
4. Run `agent/fetch_events.py` to populate initial data

## Dependencies

Python 3.8+ standard library only (no pip installs required).

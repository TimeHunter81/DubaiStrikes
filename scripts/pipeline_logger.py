#!/usr/bin/env python3
"""
Dubai Strike Monitor — append-only pipeline logger.
Writes one JSON line per event to logs/pipeline.jsonl.

Usage:
    from scripts.pipeline_logger import log
    log('main', 'event_added', id=eid, title='...', sources=['bbc.com'])
"""
import json
import os
import fcntl
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR  = os.path.join(REPO_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "pipeline.jsonl")


def log(cron: str, action: str, **kwargs):
    """Append one structured log line. Thread-safe via flock."""
    os.makedirs(LOG_DIR, exist_ok=True)
    entry = {
        "ts":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cron":   cron,
        "action": action,
        **kwargs,
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with open(LOG_FILE, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def tail(n: int = 50, cron: str = None, action: str = None) -> list:
    """Read last n log lines, optionally filtered."""
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    entries = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cron and e.get("cron") != cron:
            continue
        if action and e.get("action") != action:
            continue
        entries.append(e)
        if len(entries) >= n:
            break
    return list(reversed(entries))


if __name__ == "__main__":
    import sys
    # Quick CLI: python pipeline_logger.py [--tail N] [--cron X] [--action Y]
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--tail", type=int, default=20)
    p.add_argument("--cron", default=None)
    p.add_argument("--action", default=None)
    args = p.parse_args()
    for e in tail(args.tail, args.cron, args.action):
        print(json.dumps(e))

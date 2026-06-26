#!/usr/bin/env python3
"""
Competitive Intel Tracker - Data Collection Engine
Collects data from all configured sources, stores snapshots, detects changes.

Usage:
    python3 collect.py                  # Collect all sources
    python3 collect.py --category regulatory  # Only regulatory sources
    python3 collect.py --priority P0    # Only P0 sources
    python3 collect.py --dry-run        # Show what would be collected
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup
from deepdiff import DeepDiff

# Layer 5 API configs — for JS-rendered pages.
# Keys MUST come from environment (no defaults — this repo is public).
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"

# Paths
BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
SNAPSHOTS_DIR = BASE_DIR / "snapshots"
ALERTS_DIR = BASE_DIR / "data" / "alerts"

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
ALERTS_DIR.mkdir(parents=True, exist_ok=True)


def load_config():
    """Load sources configuration."""
    config_path = CONFIG_DIR / "sources.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_snapshot_path(category, source_id, date_str=None):
    """Get path for a snapshot file."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    cat_dir = SNAPSHOTS_DIR / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    return cat_dir / f"{source_id}_{date_str}.json"


def get_latest_snapshot(category, source_id):
    """Find the most recent snapshot for a source."""
    cat_dir = SNAPSHOTS_DIR / category
    if not cat_dir.exists():
        return None
    files = sorted(cat_dir.glob(f"{source_id}_*.json"), reverse=True)
    if not files:
        return None
    with open(files[0], "r") as f:
        return json.load(f)


def save_snapshot(category, source_id, data):
    """Save a snapshot with timestamp."""
    date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = get_snapshot_path(category, source_id, date_str)
    record = {
        "source_id": source_id,
        "collected_at": datetime.now().isoformat(),
        "data": data
    }
    with open(path, "w") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    return path


def create_alert(source_id, alert_type, title, detail, urgency="48h"):
    """Create an alert file for triggered conditions."""
    alert = {
        "source_id": source_id,
        "type": alert_type,
        "title": title,
        "detail": detail,
        "urgency": urgency,
        "created_at": datetime.now().isoformat(),
        "acknowledged": False
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = ALERTS_DIR / f"{urgency}_{source_id}_{ts}.json"
    with open(path, "w") as f:
        json.dump(alert, f, indent=2, ensure_ascii=False)

    # Also print to stdout for immediate visibility
    urgency_marker = {"24h": "!!!", "48h": "!!", "72h": "!"}.get(urgency, "")
    print(f"\n{'='*60}")
    print(f"  ALERT {urgency_marker} [{urgency}] {title}")
    print(f"  Source: {source_id}")
    print(f"  {detail}")
    print(f"{'='*60}\n")
    return path


# ============================================================
# COLLECTORS
# ============================================================

def collect_firecrawl_scrape(url, wait_for=5000):
    """
    Layer 5 — Scrape JS-rendered pages.
    Strategy: Tavily Extract (primary) → Firecrawl (fallback) → direct HTTP (last resort).
    Tavily Extract handles JS rendering and is cheaper on credits.
    """
    # --- Try Tavily Extract first ---
    try:
        payload = {
            "api_key": TAVILY_API_KEY,
            "urls": [url],
            "extract_depth": "advanced",
        }
        with httpx.Client(timeout=60) as client:
            resp = client.post(TAVILY_EXTRACT_URL, json=payload)
            resp.raise_for_status()
            result = resp.json()

        results = result.get("results", [])
        if results and results[0].get("raw_content"):
            content = results[0]["raw_content"]
            return {
                "url": url,
                "status": 200,
                "title": results[0].get("url", url),
                "text_hash": hashlib.md5(content.encode()).hexdigest(),
                "text_preview": content[:3000].strip(),
                "source_method": "tavily_extract",
            }
    except Exception as e:
        pass  # Fall through to Firecrawl

    # --- Firecrawl fallback ---
    try:
        headers = {
            "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "url": url,
            "formats": ["markdown"],
            "waitFor": wait_for,
            "onlyMainContent": True,
        }
        with httpx.Client(timeout=90) as client:
            resp = client.post(FIRECRAWL_SCRAPE_URL, json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()

        if result.get("success"):
            md = result.get("data", {}).get("markdown", "")
            metadata = result.get("data", {}).get("metadata", {})
            if md and len(md.strip()) >= 50:
                return {
                    "url": url,
                    "status": 200,
                    "title": metadata.get("title", ""),
                    "text_hash": hashlib.md5(md.encode()).hexdigest(),
                    "text_preview": md[:3000].strip(),
                    "source_method": "firecrawl",
                }
    except Exception:
        pass  # Fall through to direct HTTP

    # --- Direct HTTP last resort ---
    return collect_webpage_json(url, "")


def collect_firecrawl_json(url, prompt, schema=None, wait_for=5000):
    """
    Layer 5 — Use Firecrawl REST API with JSON extraction for structured data.
    Ideal for extracting specific fields like rates and fees from JS-rendered pages.
    """
    try:
        headers = {
            "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "url": url,
            "formats": ["json"],
            "jsonOptions": {"prompt": prompt},
            "waitFor": wait_for,
        }
        if schema:
            payload["jsonOptions"]["schema"] = schema

        with httpx.Client(timeout=60) as client:
            resp = client.post(FIRECRAWL_SCRAPE_URL, json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()

        if not result.get("success"):
            return {"url": url, "error": result.get("error", "firecrawl returned failure")}

        data = result.get("data", {})
        json_data = data.get("json", {})
        metadata = data.get("metadata", {})

        return {
            "url": url,
            "status": 200,
            "title": metadata.get("title", ""),
            "extracted": json_data,
            "text_hash": hashlib.md5(json.dumps(json_data, sort_keys=True).encode()).hexdigest(),
            "source_method": "firecrawl_json",
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


def collect_webpage_json(url, extract_prompt, schema=None):
    """
    Layer 3 — Direct HTTP + BS4 for static HTML pages (regulatory, docs).
    Will fail on JS-rendered SPAs — use collect_firecrawl_scrape for those.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36"
        }
        with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()

            return {
                "url": url,
                "status": resp.status_code,
                "title": soup.title.string if soup.title else "",
                "text_hash": hashlib.md5(soup.get_text().encode()).hexdigest(),
                "text_preview": soup.get_text()[:2000].strip(),
                "source_method": "httpx_bs4",
                "links": [
                    {"text": a.get_text(strip=True), "href": a.get("href", "")}
                    for a in soup.find_all("a", href=True)[:50]
                ]
            }
    except Exception as e:
        return {"url": url, "error": str(e)}


def collect_webpage_diff(url):
    """Collect page content for diff comparison."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36"
        }
        with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()

            text = soup.get_text(separator="\n", strip=True)
            return {
                "url": url,
                "status": resp.status_code,
                "content_hash": hashlib.md5(text.encode()).hexdigest(),
                "content": text[:5000],
            }
    except Exception as e:
        return {"url": url, "error": str(e)}


def collect_app_store(app_id, store="hk"):
    """Collect App Store listing data via iTunes Lookup API."""
    try:
        lookup_url = f"https://itunes.apple.com/lookup?id={app_id}&country={store}"
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(lookup_url)
            resp.raise_for_status()
            data = resp.json()

        if data.get("resultCount", 0) == 0:
            return {"app_id": app_id, "error": "Not found"}

        app = data["results"][0]
        return {
            "app_id": app_id,
            "store": store,
            "name": app.get("trackName", ""),
            "version": app.get("version", ""),
            "release_date": app.get("currentVersionReleaseDate", ""),
            "release_notes": app.get("releaseNotes", ""),
            "rating": app.get("averageUserRating", 0),
            "rating_count": app.get("userRatingCount", 0),
            "rating_current_version": app.get("averageUserRatingForCurrentVersion", 0),
            "price": app.get("formattedPrice", ""),
            "description_preview": app.get("description", "")[:500],
            "bundle_id": app.get("bundleId", ""),
            "file_size": app.get("fileSizeBytes", ""),
            "minimum_os": app.get("minimumOsVersion", ""),
            "genres": app.get("genres", []),
        }
    except Exception as e:
        return {"app_id": app_id, "error": str(e)}


def collect_job_search(search_query, watch_keywords=None):
    """Layer 2/4 — Use Tavily REST API for job posting search."""
    # Try Tavily REST API directly (more reliable than CLI in venv)
    try:
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": f"{search_query} jobs hiring 2026",
            "max_results": 5,
            "search_depth": "basic",
        }
        with httpx.Client(timeout=30) as client:
            resp = client.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        matched = []
        if watch_keywords and results:
            combined = " ".join(r.get("content", "") for r in results).lower()
            matched = [kw for kw in watch_keywords if kw.lower() in combined]

        return {
            "query": search_query,
            "result_count": len(results),
            "raw_results": json.dumps(
                [{"title": r.get("title", ""), "url": r.get("url", ""),
                  "snippet": r.get("content", "")[:200]}
                 for r in results], ensure_ascii=False
            )[:3000],
            "keywords_matched": matched,
            "source_method": "tavily_api",
        }
    except Exception as e:
        return {"query": search_query, "error": f"tavily API: {str(e)}"}


# ============================================================
# DIFF & TRIGGER ENGINE
# ============================================================

def check_diff(category, source_id, new_data):
    """Compare new data with latest snapshot, return changes."""
    prev = get_latest_snapshot(category, source_id)
    if prev is None:
        return {"status": "first_collection", "changes": None}

    prev_data = prev.get("data", {})

    # For hash-based comparison
    old_hash = prev_data.get("content_hash") or prev_data.get("text_hash")
    new_hash = new_data.get("content_hash") or new_data.get("text_hash")

    if old_hash and new_hash:
        if old_hash == new_hash:
            return {"status": "no_change", "changes": None}
        else:
            return {"status": "changed", "changes": "content_hash_changed"}

    # For structured data comparison
    try:
        diff = DeepDiff(prev_data, new_data, ignore_order=True,
                       exclude_paths=["root['collected_at']", "root['text_preview']",
                                      "root['content']", "root['description_preview']",
                                      "root['raw_results']"])
        if diff:
            return {"status": "changed", "changes": diff.to_dict()}
        return {"status": "no_change", "changes": None}
    except Exception as e:
        return {"status": "error", "changes": str(e)}


def check_triggers(source_config, new_data, diff_result):
    """Check if any trigger conditions are met."""
    alerts = []
    triggers = source_config.get("triggers", [])

    text_content = ""
    if isinstance(new_data, dict):
        text_content = json.dumps(new_data, ensure_ascii=False).lower()

    for trigger in triggers:
        # Pattern-based trigger (keyword in content)
        pattern = trigger.get("pattern", "")
        if pattern and pattern.lower() in text_content:
            # Only alert if content changed
            if diff_result.get("status") == "changed":
                alerts.append({
                    "type": "pattern_match",
                    "pattern": pattern,
                    "urgency": trigger.get("urgency", "72h")
                })

        # Field-based trigger (specific value changed)
        field = trigger.get("field", "")
        baseline = trigger.get("baseline", "")
        if field and baseline and diff_result.get("status") == "changed":
            alerts.append({
                "type": "field_change",
                "field": field,
                "baseline": baseline,
                "urgency": trigger.get("urgency", "48h")
            })

    return alerts


# ============================================================
# MAIN COLLECTION ORCHESTRATOR
# ============================================================

def collect_category(category, sources, dry_run=False):
    """Collect all sources in a category."""
    results = {}
    for source_id, config in sources.items():
        source_type = config.get("type", "scrape_json")
        url = config.get("url", "")
        name = config.get("name", source_id)

        print(f"  [{category}/{source_id}] {name}...", end=" ", flush=True)

        if dry_run:
            print("(dry-run skip)")
            continue

        # Collect based on type
        # Layer routing: firecrawl for JS-rendered pages, httpx for static
        data = None
        if source_type == "firecrawl_json":
            # Layer 5 — Firecrawl with structured JSON extraction
            data = collect_firecrawl_json(
                url,
                config.get("schema", {}).get("extract", "Extract all rates and fees"),
                config.get("schema", {}).get("json_schema"),
                config.get("wait_for", 5000),
            )
        elif source_type == "firecrawl_scrape":
            # Layer 5 — Firecrawl for JS-rendered pages (markdown)
            data = collect_firecrawl_scrape(url, config.get("wait_for", 5000))
        elif source_type == "scrape_json":
            # Layer 3 — Direct HTTP for static pages
            data = collect_webpage_json(url, config.get("schema", {}).get("extract", ""))
        elif source_type == "scrape_diff":
            # Layer 3 — Direct HTTP diff comparison
            data = collect_webpage_diff(url)
        elif source_type == "app_store":
            # Layer 2 — iTunes Lookup API
            data = collect_app_store(config.get("app_id"), config.get("store", "hk"))
        elif source_type == "search":
            # Layer 3 — Tavily CLI search
            data = collect_job_search(
                config.get("search_query", ""),
                config.get("watch_keywords")
            )
        else:
            print(f"(unknown type: {source_type})")
            continue

        if data and "error" not in data:
            # Check diff
            diff = check_diff(category, source_id, data)
            print(f"[{diff['status']}]", end="")

            # Check triggers
            alerts = check_triggers(config, data, diff)
            if alerts:
                for alert in alerts:
                    create_alert(
                        source_id,
                        alert["type"],
                        f"{name}: {alert.get('pattern', alert.get('field', 'change detected'))}",
                        json.dumps(alert, ensure_ascii=False),
                        alert.get("urgency", "48h")
                    )
                print(f" [{len(alerts)} alert(s)]")
            else:
                print()

            # Save snapshot
            save_snapshot(category, source_id, data)
            results[source_id] = {"status": "ok", "diff": diff["status"]}
        else:
            error = data.get("error", "unknown") if data else "no data"
            print(f"[ERROR: {error}]")
            results[source_id] = {"status": "error", "error": error}

        # Rate limiting
        time.sleep(1)

    return results


def main():
    parser = argparse.ArgumentParser(description="Competitive Intel Data Collector")
    parser.add_argument("--category", "-c",
                       choices=["regulatory", "competitor_rates", "app_store",
                                "job_postings", "competitor_docs"],
                       help="Only collect from this category")
    parser.add_argument("--priority", "-p",
                       choices=["P0", "T0", "T1", "signal"],
                       help="Only collect sources with this priority")
    parser.add_argument("--dry-run", "-n", action="store_true",
                       help="Show what would be collected without actually collecting")
    parser.add_argument("--quick", "-q", action="store_true",
                       help="Only P0 and T0 sources (daily quick scan)")
    args = parser.parse_args()

    config = load_config()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"  Competitive Intel Tracker - Collection Run")
    print(f"  {timestamp}")
    print(f"{'='*60}\n")

    all_results = {}

    for category, sources in config.items():
        if not isinstance(sources, dict):
            continue

        # Category filter
        if args.category and category != args.category:
            continue

        # Quick mode: only P0 + T0
        if args.quick:
            sources = {
                k: v for k, v in sources.items()
                if v.get("priority") in ("P0", "T0")
            }
            if not sources:
                continue

        # Priority filter
        if args.priority:
            sources = {
                k: v for k, v in sources.items()
                if v.get("priority") == args.priority
            }
            if not sources:
                continue

        print(f"\n--- {category.upper()} ({len(sources)} sources) ---")
        results = collect_category(category, sources, dry_run=args.dry_run)
        all_results[category] = results

    # Summary
    print(f"\n{'='*60}")
    print(f"  COLLECTION SUMMARY")
    print(f"{'='*60}")
    total = sum(len(v) for v in all_results.values())
    ok = sum(1 for cat in all_results.values() for r in cat.values() if r.get("status") == "ok")
    changed = sum(1 for cat in all_results.values() for r in cat.values() if r.get("diff") == "changed")
    errors = sum(1 for cat in all_results.values() for r in cat.values() if r.get("status") == "error")
    print(f"  Total: {total} | OK: {ok} | Changed: {changed} | Errors: {errors}")

    # Check for pending alerts
    pending_alerts = list(ALERTS_DIR.glob("*.json"))
    unacked = [a for a in pending_alerts if not json.load(open(a)).get("acknowledged")]
    if unacked:
        print(f"\n  PENDING ALERTS: {len(unacked)}")
        for a in sorted(unacked):
            alert = json.load(open(a))
            print(f"    [{alert['urgency']}] {alert['title']}")

    print()

    # Save run log
    run_log = {
        "timestamp": datetime.now().isoformat(),
        "results": {
            cat: {sid: r for sid, r in results.items()}
            for cat, results in all_results.items()
        },
        "summary": {"total": total, "ok": ok, "changed": changed, "errors": errors}
    }
    log_path = DATA_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(log_path, "w") as f:
        json.dump(run_log, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()

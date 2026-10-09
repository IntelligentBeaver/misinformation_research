#!/usr/bin/env python3
"""
Unified detail (article) scraper for all sources.

Replaces the per-source scripts:
    harvard_page_details.py, webmd_article.py, webmd_healthtopics.py,
    who_disease_outbreak_details.py, who_fact_sheet.py,
    who_feature_stories_details.py, who_news_details.py

All sources are driven by config.py (DETAIL_SOURCES) and extractors.py.

Usage:
    python scrape_all.py --list
    python scrape_all.py --source harvard_details
    python scrape_all.py --source who_news_details --recent-days 30
    python scrape_all.py --source who_disease_outbreak_details --min-year 2020 --threads 8
    python scrape_all.py --source harvard_details --test-mode --test-count 3
    python scrape_all.py --source who_fact_sheets --limit 5

Dependencies:
    pip install requests beautifulsoup4 lxml

Optional (selenium method override):
    pip install selenium webdriver-manager

Architecture:
    - load_input_items: reads URLs from file, year directories, or index page
    - apply_filters: min_year, recent_days, max_articles, test_mode
    - process_item: fetch HTML (engine or internal) -> parse -> save per-article + aggregate
    - Concurrency: sequential or threaded (ThreadPoolExecutor)
    - Resume: skips existing per-article files and URLs in year-grouped outputs
    - Error handling: saves .error.json, continues on error (configurable)

See scraping/README.md for full documentation.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

# Allow running this file directly from any working directory:
#   python scraping/detail_scraping/scrape_all.py --source harvard_details
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent  # misinformation_research/
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scraping.common import (
    extract_year_from_date,
    fetch_html,
    fetch_html_bs4,
    load_json,
    load_json_list,
    make_slug,
    resolve_path,
    save_json,
    utc_now_iso,
)
from scraping.detail_scraping.config import get_source_config, list_source_names
from scraping.detail_scraping.extractors import get_parser

DETAIL_CONFIG_DIR = Path(__file__).resolve().parent


def base_origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


# ----------------------------
# Input loading
# ----------------------------

def load_year_files(input_dir: Path) -> List[dict]:
    """Load all records from year JSON files (e.g. 2024.json, 2025.json)."""
    items: List[dict] = []
    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}")
        return items

    for filepath in sorted(input_dir.glob("*.json"), reverse=True):
        if not filepath.stem or not filepath.stem[0].isdigit():
            continue
        try:
            data = load_json(filepath)
            if isinstance(data, list):
                items.extend(row for row in data if isinstance(row, dict) and row.get("url"))
                print(f"Loaded {len(data)} records from {filepath.name}")
        except Exception as exc:
            print(f"Error loading {filepath}: {exc}")

    return items


def discover_index_links(cfg: Dict[str, Any]) -> List[dict]:
    """Discover detail-page links from an index page (fact sheets)."""
    input_cfg = cfg["input"]
    index_url = input_cfg["index_url"]
    link_prefix = input_cfg.get("link_prefix", "")

    html = fetch_html_bs4(index_url, cfg)
    soup = BeautifulSoup(html, "lxml")
    base = base_origin(index_url)

    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(link_prefix):
            links.add(urljoin(base, href))

    print(f"Found {len(links)} detail-page links (unique)")
    return [{"url": link} for link in sorted(links)]


def load_input_items(cfg: Dict[str, Any]) -> List[dict]:
    input_cfg = cfg["input"]
    mode = input_cfg.get("mode", "file")

    if mode == "file":
        input_path = resolve_path(input_cfg["path"], DETAIL_CONFIG_DIR)
        print(f"Loading input items from {input_path}")
        return load_json_list(input_path)

    if mode == "year_files":
        input_dir = resolve_path(input_cfg["dir"], DETAIL_CONFIG_DIR)
        return load_year_files(input_dir)

    if mode == "index":
        return discover_index_links(cfg)

    raise ValueError(f"Unknown input mode '{mode}'")


# ----------------------------
# Filters
# ----------------------------

def item_date(item: dict) -> Optional[str]:
    for key in ("date", "published_date"):
        value = item.get(key)
        if value:
            return value
    return None


def apply_filters(items: List[dict], cfg: Dict[str, Any], overrides) -> List[dict]:
    filters = cfg.get("filters", {})

    min_year = overrides.min_year if overrides.min_year is not None else filters.get("min_year")
    recent_days = overrides.recent_days if overrides.recent_days is not None else filters.get("recent_days")
    max_articles = overrides.max_articles if overrides.max_articles is not None else filters.get("max_articles")
    limit = overrides.limit if overrides.limit is not None else filters.get("limit")

    if min_year is not None or recent_days is not None:
        cutoff_date = None
        if recent_days is not None:
            cutoff_date = (datetime.now() - timedelta(days=int(recent_days))).date()

        filtered = []
        for item in items:
            date_str = item_date(item)
            year = extract_year_from_date(date_str) if date_str else None

            if min_year is not None and (year is None or year < int(min_year)):
                continue
            if cutoff_date is not None:
                try:
                    item_date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                if item_date_obj < cutoff_date:
                    continue
            filtered.append(item)
        items = filtered

    if overrides.test_mode:
        items = items[: max(1, int(overrides.test_count or 1))]
    if limit is not None:
        items = items[: int(limit)]
    if max_articles is not None:
        items = items[: int(max_articles)]

    return items


# ----------------------------
# Existing-output helpers
# ----------------------------

def load_existing_urls(output_dir: Path) -> set:
    existing_urls: set = set()
    if not output_dir.exists():
        return existing_urls

    for filepath in output_dir.glob("*.json"):
        if not filepath.stem or not filepath.stem[0].isdigit():
            continue
        try:
            data = load_json(filepath)
            if isinstance(data, list):
                for row in data:
                    if isinstance(row, dict) and row.get("url"):
                        existing_urls.add(row["url"])
        except Exception:
            continue

    return existing_urls


# ----------------------------
# Item processing
# ----------------------------

def process_item(item: dict, cfg: Dict[str, Any], context: Dict[str, Any], idx: int, total: int) -> Optional[dict]:
    url = (item.get("url") or "").strip()
    if not url:
        return None

    output = cfg["output"]
    output_dir = context["output_dir"]
    parser = get_parser(cfg["parser"])
    slug = item.get("slug") or make_slug(url)

    # Skip already-scraped per-article files
    if output.get("per_article") and output.get("skip_existing"):
        out_file = output_dir / f"{slug}.json"
        if out_file.exists():
            try:
                existing = load_json(out_file)
                if isinstance(existing, dict):
                    print(f"[{idx}/{total}] Skipped existing: {slug}")
                    return existing
            except Exception:
                pass

    # Skip URLs already present in year-grouped output files
    if output.get("group_by_year") and output.get("skip_existing"):
        if url in context["existing_urls"]:
            print(f"[{idx}/{total}] Already extracted: {str(item.get('title', ''))[:50]}")
            return None

    # Politeness delay for parsers that fetch pages themselves
    if cfg.get("fetch") != "engine":
        delay = float(cfg.get("request", {}).get("delay", 0) or 0)
        if delay > 0:
            time.sleep(delay)

    try:
        if cfg.get("fetch") == "engine":
            method = cfg.get("method", "bs4")
            html = fetch_html(url, cfg, method=method)
            result = parser(item, html, url, cfg)
        else:
            result = parser(item, cfg)

        if result is None:
            return None

        if output.get("per_article"):
            save_json(result, output_dir / f"{slug}.json")
            print(f"[{idx}/{total}] Saved: {slug}.json")

        return result

    except Exception as exc:
        error_obj = {
            "url": url,
            "slug": slug,
            "error": str(exc),
            "scrape_timestamp_utc": utc_now_iso(),
        }
        if output.get("per_article"):
            save_json(error_obj, output_dir / f"{slug}.error.json")

        if output.get("continue_on_error", True):
            print(f"[{idx}/{total}] Error: {slug} -> {exc}")
            return None
        raise


def process_sequential(
    cfg: Dict[str, Any],
    items: List[dict],
    context: Dict[str, Any],
) -> List[dict]:
    results: List[dict] = []
    total = len(items)
    for idx, item in enumerate(items, 1):
        outcome = process_item(item, cfg, context, idx, total)
        if outcome is not None:
            results.append(outcome)
    return results


def process_threaded(
    cfg: Dict[str, Any],
    items: List[dict],
    context: Dict[str, Any],
    overrides,
) -> List[dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from threading import Lock

    workers = int(overrides.workers or cfg.get("workers", 4))
    save_every = int(cfg["output"].get("save_every", 5))
    output = cfg["output"]
    output_dir = context["output_dir"]

    results: List[dict] = []
    results_lock = Lock()
    total = len(items)

    print(f"Starting {workers} worker threads for {total} items...")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_item, item, cfg, context, idx, total): idx
            for idx, item in enumerate(items, 1)
        }
        for future in as_completed(futures):
            try:
                outcome = future.result()
            except Exception as exc:
                print(f"Unhandled error: {exc}")
                continue

            with results_lock:
                if outcome is not None:
                    results.append(outcome)

                if (
                    output.get("group_by_year")
                    and save_every > 0
                    and len(results) > 0
                    and len(results) % save_every == 0
                ):
                    save_by_year(results, output_dir)
                    print(f"  Progress saved ({len(results)} articles)")

    return results


# ----------------------------
# Output
# ----------------------------

def year_of(record: dict) -> str:
    date_str = record.get("published_date") or record.get("date") or ""
    year = extract_year_from_date(date_str)
    return str(year) if year else "unknown"


def save_by_year(records: List[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    grouped: Dict[str, List[dict]] = {}
    for record in records:
        grouped.setdefault(year_of(record), []).append(record)

    def sort_key(item):
        year_str = item[0]
        if year_str == "unknown":
            return (1, 0)
        try:
            return (0, -int(year_str))
        except ValueError:
            return (1, 0)

    for year, items in sorted(grouped.items(), key=sort_key):
        save_json(items, output_dir / f"{year}.json")
        print(f"  Saved {len(items)} articles to {year}.json")


def save_outputs(results: List[dict], cfg: Dict[str, Any], overrides, context: Dict[str, Any]) -> None:
    output = cfg["output"]
    output_dir = context["output_dir"]

    if output.get("group_by_year"):
        save_by_year(results, output_dir)
        years = sorted({year_of(r) for r in results})
        print(f"Saved {len(results)} total articles to {output_dir} (years: {', '.join(years)})")

        new_articles_dir = output.get("new_articles_dir")
        if new_articles_dir and results:
            target_dir = resolve_path(new_articles_dir, DETAIL_CONFIG_DIR)
            filename = f"new_articles_{time.strftime('%Y%m%d_%H%M%S')}.json"
            save_json(results, target_dir / filename)
            print(f"Saved {len(results)} new articles from this run to {target_dir / filename}")
        return

    aggregate = output.get("aggregate")
    if aggregate:
        save_json(results, output_dir / aggregate)
        print(f"Wrote {len(results)} article records to: {output_dir / aggregate}")


# ----------------------------
# Engine
# ----------------------------

def run_source(source_name: str, cfg: Dict[str, Any], overrides) -> None:
    items = load_input_items(cfg)
    items = apply_filters(items, cfg, overrides)

    output = cfg["output"]
    output_dir = resolve_path(output["dir"], DETAIL_CONFIG_DIR)

    context: Dict[str, Any] = {"output_dir": output_dir, "existing_urls": set()}
    if output.get("skip_existing"):
        context["existing_urls"] = load_existing_urls(output_dir)
        if context["existing_urls"]:
            print(f"Found {len(context['existing_urls'])} existing articles to skip")

    print(f"Processing {len(items)} articles...")

    concurrency = cfg.get("concurrency", "sequential")
    if concurrency == "threaded":
        results = process_threaded(cfg, items, context, overrides)
    else:
        results = process_sequential(cfg, items, context)

    save_outputs(results, cfg, overrides, context)

    print(f"Done. {len(results)} article records collected.")


# ----------------------------
# CLI
# ----------------------------

def apply_overrides(cfg: Dict[str, Any], args) -> Dict[str, Any]:
    if args.method:
        cfg["method"] = args.method

    if args.input:
        cfg.setdefault("input", {})["path"] = args.input

    if args.output_dir:
        cfg.setdefault("output", {})["dir"] = args.output_dir
    if args.aggregate is not None:
        cfg.setdefault("output", {})["aggregate"] = args.aggregate
    if args.no_skip_existing:
        cfg.setdefault("output", {})["skip_existing"] = False
    if args.stop_on_error:
        cfg.setdefault("output", {})["continue_on_error"] = False

    request_cfg = cfg.setdefault("request", {})
    if args.request_delay is not None:
        request_cfg["delay"] = args.request_delay
    if args.request_timeout is not None:
        request_cfg["timeout"] = args.request_timeout
    if args.user_agent:
        request_cfg["user_agent"] = args.user_agent

    filters = cfg.setdefault("filters", {})
    if args.min_year is not None:
        filters["min_year"] = args.min_year
    if args.recent_days is not None:
        filters["recent_days"] = args.recent_days
    if args.max_articles is not None:
        filters["max_articles"] = args.max_articles
    if args.limit is not None:
        filters["limit"] = args.limit
    if args.test_mode:
        filters["test_mode"] = True
    if args.test_count is not None:
        filters["test_count"] = args.test_count

    if args.workers is not None:
        cfg["workers"] = args.workers

    return cfg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified detail scraper for all sources (driven by config.py)"
    )
    source_group = parser.add_mutually_exclusive_group(required=False)
    source_group.add_argument("--source", help="Source name to scrape (see --list)")
    source_group.add_argument("--all", action="store_true", help="Run all sources")
    parser.add_argument("--list", action="store_true", help="List available source names and exit")

    parser.add_argument("--method", choices=["bs4", "selenium", "auto"],
                        help="Override the fetch method (engine-fetched sources only)")
    parser.add_argument("--input", help="Override input JSON path (file-mode sources)")
    parser.add_argument("--output-dir", help="Override output directory")
    parser.add_argument("--aggregate", help="Override aggregate output filename")
    parser.add_argument("--min-year", type=int, help="Only process articles from this year onwards")
    parser.add_argument("--recent-days", type=int, help="Only process articles from the last N days")
    parser.add_argument("--max-articles", type=int, help="Maximum number of articles to process")
    parser.add_argument("--limit", type=int, help="Limit number of input items (index-mode sources)")
    parser.add_argument("--test-mode", action="store_true", help="Only process the first N records")
    parser.add_argument("--test-count", type=int, help="Number of records to process in test mode")
    parser.add_argument("--workers", type=int, help="Number of worker threads (threaded sources)")
    parser.add_argument("--request-delay", type=float, help="Delay between requests (seconds)")
    parser.add_argument("--request-timeout", type=int, help="HTTP timeout (seconds)")
    parser.add_argument("--user-agent", help="HTTP User-Agent string")
    parser.add_argument("--no-skip-existing", action="store_true", help="Re-scrape articles that already exist")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop immediately when any article fails")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.list:
        print("Available sources:")
        for name in list_source_names():
            print(f"  {name}")
        return

    if args.all:
        names = list_source_names()
    elif args.source:
        names = [args.source]
    else:
        parser.error("one of the arguments --source --all is required")

    for name in names:
        cfg = get_source_config(name)
        cfg = apply_overrides(cfg, args)

        print("=" * 60)
        print(f"Scraping source: {name}")
        print("=" * 60)

        try:
            run_source(name, cfg, args)
        except Exception as exc:
            print(f"ERROR: source '{name}' failed: {exc}")
            raise


if __name__ == "__main__":
    main()

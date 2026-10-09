#!/usr/bin/env python3
"""
Unified list (headline/index) scraper for all sources.

Replaces the per-source scripts:
    harvard_blogs_list.py, harvard_decision_guides_list.py,
    harvard_healthtopics_list.py, harvard_medicalprocedures_list.py,
    webmd_article_headlines.py, webmd_healthtopics_headlines.py,
    who_disease_outbreak_headlines.py, who_feature_stories_headlines.py,
    who_news_headlines.py

All sources are driven by config.py (LIST_SOURCES) and extractors.py.

Usage:
    python scrape_all.py --list
    python scrape_all.py --source harvard_blogs
    python scrape_all.py --source who_disease_outbreak_headlines --min-year 2020
    python scrape_all.py --source webmd_healthtopics --letters a b c
    python scrape_all.py --all --method bs4

Dependencies:
    pip install requests beautifulsoup4 lxml

Optional (selenium / playwright modes):
    pip install selenium webdriver-manager playwright
    playwright install chromium

Architecture:
    - PAGINATION_MODES maps 6 pagination strategies to runner functions
    - Each runner: fetch page -> extract -> filter -> save
    - Output: JSON files grouped by year or single file (config-driven)
    - Resume support: merges with existing output, skips duplicates by URL

See scraping/README.md for full documentation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

import requests

# Allow running this file directly from any working directory:
#   python scraping/list_scraping/scrape_all.py --source harvard_blogs
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent  # misinformation_research/
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scraping.common import (
    CONFIG_DIR,
    extract_year_from_date,
    fetch_and_extract,
    load_json,
    load_json_list,
    requests_session_with_retries,
    resolve_path,
    save_json,
    setup_selenium_driver,
    urls_from_records,
)
from scraping.list_scraping.config import get_source_config, list_source_names
from scraping.list_scraping.extractors import get_extractor


# ----------------------------
# Pagination helpers
# ----------------------------

def page_url(base_url: str, page: int, param: str = "page") -> str:
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode({param: page})}"


def build_az_url(base_url: str, letter: str) -> str:
    letter = letter.lower()
    if letter == "a":
        return base_url
    return f"{base_url}?pg={letter}"


def parse_letters_arg(letters_arg: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"[,\s]+", letters_arg) if p.strip()]
    if not parts:
        return []
    if len(parts) == 1 and parts[0].lower() == "all":
        return [chr(c) for c in range(ord("a"), ord("z") + 1)]
    out: List[str] = []
    for part in parts:
        if "," in part:
            out.extend(p.strip() for p in part.split(",") if p.strip())
        else:
            out.append(part)
    return out


def get_total_pages(driver, pager_selector: str = "#ppager") -> int:
    from selenium.webdriver.common.by import By

    try:
        pager = driver.find_element(By.CSS_SELECTOR, pager_selector)
        match = re.search(r"of\s+(\d+)", pager.text)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return 1


def click_next_page(driver, next_button_selector: str) -> bool:
    from selenium.webdriver.common.by import By

    try:
        next_button = driver.find_element(By.CSS_SELECTOR, next_button_selector)
        if next_button:
            driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", next_button)
            return True
    except Exception:
        pass
    return False


# ----------------------------
# Existing-output helpers
# ----------------------------

def load_existing_by_year(output_dir: Path):
    existing_by_year: Dict[str, List[dict]] = {}
    existing_urls: set = set()
    if not output_dir.exists():
        return existing_by_year, existing_urls

    for filepath in output_dir.glob("*.json"):
        if not filepath.stem or not filepath.stem[0].isdigit():
            continue
        try:
            headlines = load_json(filepath)
            if not isinstance(headlines, list):
                continue
            existing_by_year[filepath.stem] = headlines
            for item in headlines:
                if isinstance(item, dict) and item.get("url"):
                    existing_urls.add(item["url"])
        except Exception:
            continue

    return existing_by_year, existing_urls


# ----------------------------
# Pagination modes
# ----------------------------

def run_single_page(source_name: str, cfg: Dict[str, Any], overrides) -> List[dict]:
    method = overrides.method or cfg["method"]
    extractor = get_extractor(source_name)
    return fetch_and_extract(cfg["target_url"], cfg, extractor, method=method)


def run_url_param_pagination(source_name: str, cfg: Dict[str, Any], overrides) -> List[dict]:
    pagination = cfg["pagination"]
    param = pagination.get("param", "page")
    page = int(overrides.start_page or pagination.get("start_page", 1))
    max_pages = pagination.get("max_pages")
    if overrides.max_pages is not None:
        max_pages = int(overrides.max_pages)
    end_page = overrides.end_page or pagination.get("end_page")
    stop_on_empty = bool(pagination.get("stop_on_empty_page", True))
    stop_on_duplicate = bool(pagination.get("stop_on_existing_duplicate", False))

    output = cfg["output"]
    output_path = resolve_path(output["dir"], CONFIG_DIR) / output.get("filename", "output.json")

    existing_records: List[dict] = []
    existing_urls: set = set()
    if output.get("merge_existing") and output_path.exists():
        existing_records = load_json_list(output_path)
        existing_urls = urls_from_records(existing_records)

    method = overrides.method or cfg["method"]
    extractor = get_extractor(source_name)
    session = requests_session_with_retries(
        cfg.get("request", {}).get("user_agent", "Mozilla/5.0")
    )

    all_records: List[dict] = []
    seen_in_run: set = set()
    pages_scraped = 0

    while True:
        if end_page is not None and page > int(end_page):
            break
        if max_pages is not None and pages_scraped >= int(max_pages):
            break

        url = page_url(cfg["target_url"], page, param)
        page_records = fetch_and_extract(url, cfg, extractor, method=method, session=session)

        if not page_records and stop_on_empty:
            break

        duplicate_found = False
        for record in page_records:
            record_url = record.get("url")
            if not record_url or record_url in seen_in_run:
                continue
            if record_url in existing_urls:
                duplicate_found = True
                continue
            seen_in_run.add(record_url)
            all_records.append(record)
            print(f"Extracted: {record.get('title', '')}")

        if stop_on_duplicate and duplicate_found:
            break

        page += 1
        pages_scraped += 1

    session.close()

    if output.get("merge_existing"):
        return all_records + existing_records
    return all_records


def run_url_path_pagination(source_name: str, cfg: Dict[str, Any], overrides) -> List[dict]:
    pagination = cfg["pagination"]
    base_url = cfg["target_url"].rstrip("/")
    page = int(overrides.start_page or pagination.get("start_page", 1))
    end_page = overrides.end_page or pagination.get("end_page")
    stop_on_empty = bool(pagination.get("stop_on_empty_page", True))

    max_age_days = cfg.get("filters", {}).get("max_age_days")
    if overrides.max_age_days is not None:
        max_age_days = int(overrides.max_age_days)
    cutoff_date = None
    if max_age_days:
        cutoff_date = datetime.utcnow().date() - timedelta(days=int(max_age_days))

    method = overrides.method or cfg["method"]
    extractor = get_extractor(source_name)
    session = requests_session_with_retries(
        cfg.get("request", {}).get("user_agent", "Mozilla/5.0")
    )

    all_items: List[dict] = []
    seen_urls: set = set()

    while True:
        url = base_url if page == 1 else f"{base_url}/{page}"
        print(f"Scraping page {page}: {url}")

        try:
            items = fetch_and_extract(url, cfg, extractor, method=method, session=session)
        except requests.RequestException as exc:
            print(f"Request error for {url}: {exc}. Stopping.")
            break

        if not items and stop_on_empty:
            print("No items found on page (or container missing). Stopping.")
            break

        kept = 0
        for item in items:
            item_url = item.get("url")
            if not item_url or item_url in seen_urls:
                continue

            date_iso = item.get("date")
            item_date = None
            if date_iso:
                try:
                    item_date = datetime.fromisoformat(date_iso).date()
                except Exception:
                    item_date = None

            if item_date is None:
                continue
            if cutoff_date and item_date < cutoff_date:
                continue

            seen_urls.add(item_url)
            all_items.append(item)
            kept += 1

        print(f"  found {len(items)} items on page, kept {kept} after filtering")

        page += 1
        if end_page is not None and page > int(end_page):
            print("Reached end_page limit. Stopping.")
            break

    session.close()
    return all_items


def run_letters_pagination(source_name: str, cfg: Dict[str, Any], overrides) -> List[dict]:
    pagination = cfg["pagination"]
    letters = parse_letters_arg(overrides.letters) if overrides.letters else pagination.get("letters", [])
    if not letters:
        letters = ["a"]

    base_index_url = cfg["target_url"]
    method = overrides.method or cfg["method"]
    extractor = get_extractor(source_name)
    session = requests_session_with_retries(
        cfg.get("request", {}).get("user_agent", "Mozilla/5.0")
    )

    all_items: List[dict] = []
    for letter in letters:
        url = build_az_url(base_index_url, letter)
        print(f"Scraping letter '{letter}': {url}")
        try:
            items = fetch_and_extract(url, cfg, extractor, method=method, session=session)
        except Exception as exc:
            print(f"Error scraping letter '{letter}': {exc}")
            continue
        all_items.extend(items)

    session.close()
    return all_items


def canonicalize_url(url: str) -> str:
    from urllib.parse import urlunparse, urlparse

    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def normalize_title(title: str) -> str:
    title = (title or "").lower()
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def webmd_search_worker(keyword: str, cfg: Dict[str, Any]) -> List[dict]:
    """Playwright worker: scrape WebMD search results for one keyword."""
    from multiprocessing import current_process

    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    pagination = cfg["pagination"]
    selectors = cfg["selectors"]
    tags = cfg.get("record", {}).get("tags", [])
    base_search_url = pagination["base_search_url"]
    filter_type = pagination["filter_type"]
    max_pages = int(pagination.get("max_pages_per_keyword", 2))
    delay = float(cfg.get("request", {}).get("delay", 0.1))

    print(f"[{current_process().name}] Scraping: {keyword}")
    results: List[dict] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            page.route(
                "**/*",
                lambda route, request: route.abort()
                if request.resource_type in {"image", "font", "media"}
                else route.continue_(),
            )

            for pg in range(1, max_pages + 1):
                search_url = (
                    f"{base_search_url}?query={quote(keyword)}&filter={filter_type}&pg={pg}"
                )
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector(
                        selectors.get("item", "div.search-results-item"), timeout=20000
                    )
                except PlaywrightTimeout:
                    print(f"  Timeout on page {pg}")
                    break

                items = page.query_selector_all(selectors.get("item", "div.search-results-item"))
                if not items:
                    print(f"  No results on page {pg}, stopping pagination for '{keyword}'")
                    break

                for item in items:
                    ctype = item.query_selector(selectors.get("ctype", "div.search-results-ctype"))
                    if not ctype or ctype.inner_text().strip() != "Article":
                        continue

                    link = item.query_selector(
                        selectors.get("link", "a.search-results-title-link")
                    )
                    if not link:
                        continue

                    title = link.inner_text().strip()
                    url = link.get_attribute("href")
                    desc_el = item.query_selector(
                        selectors.get("description", "div.search-results-description")
                    )
                    description = desc_el.inner_text().strip() if desc_el else None

                    results.append(
                        {
                            "title": title,
                            "url": canonicalize_url(url),
                            "description": description,
                            "tags": tags,
                            "source_keywords": [keyword],
                            "norm_title": normalize_title(title),
                        }
                    )

                time.sleep(delay)

            context.close()
            browser.close()
    except Exception as exc:
        print(f"[ERROR] Keyword '{keyword}' failed: {exc}")

    return results


def merge_search_results(flat_results: List[dict]) -> List[dict]:
    articles_by_url: Dict[str, dict] = {}
    articles_by_title: Dict[str, dict] = {}

    for item in flat_results:
        clean_url = item["url"]
        norm_title = item["norm_title"]
        keyword = item["source_keywords"][0]

        if clean_url in articles_by_url:
            articles_by_url[clean_url]["source_keywords"].append(keyword)
            continue

        if norm_title in articles_by_title:
            existing = articles_by_title[norm_title]
            existing["source_keywords"].append(keyword)
            articles_by_url[existing["url"]] = existing
            continue

        record = {
            "title": item["title"],
            "url": clean_url,
            "description": item["description"],
            "tags": item["tags"],
            "source_keywords": item["source_keywords"],
        }
        articles_by_url[clean_url] = record
        articles_by_title[norm_title] = record

    final_articles = []
    for article in articles_by_url.values():
        article["source_keywords"] = sorted(set(article["source_keywords"]))
        final_articles.append(article)
    return final_articles


def run_search_pagination(source_name: str, cfg: Dict[str, Any], overrides) -> List[dict]:
    """WebMD search mode: multiprocessing + Playwright over keywords from an input JSON."""
    from multiprocessing import Pool

    pagination = cfg["pagination"]
    input_json = resolve_path(pagination["input_json"], CONFIG_DIR)

    with input_json.open("r", encoding="utf-8") as f:
        data = json.load(f)
    keywords = sorted(
        set(item["title"].strip() for item in data if isinstance(item, dict) and "title" in item)
    )
    print(f"[INFO] Loaded {len(keywords)} keywords")

    if overrides.max_pages is not None:
        pagination["max_pages_per_keyword"] = int(overrides.max_pages)
    num_processes = int(overrides.num_processes or pagination.get("num_processes", 8))

    with Pool(processes=num_processes) as pool:
        all_results = pool.starmap(
            webmd_search_worker, [(keyword, cfg) for keyword in keywords]
        )

    flat_results = [item for sublist in all_results for item in sublist]
    return merge_search_results(flat_results)


def run_selenium_click_pagination(source_name: str, cfg: Dict[str, Any], overrides) -> List[dict]:
    """WHO list pages: Selenium renders the page, then clicks the pager's next button."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    pagination = cfg["pagination"]
    min_year = int(overrides.min_year or pagination.get("min_year", 2000))
    total_pages_selector = pagination.get("total_pages_selector", "#ppager")
    next_button_selector = pagination.get(
        "next_button_selector",
        "#ppager a.k-link.k-pager-nav[aria-label='Go to the next page']:not(.k-state-disabled)",
    )

    output = cfg["output"]
    output_dir = resolve_path(output["dir"], CONFIG_DIR)

    existing_by_year: Dict[str, List[dict]] = {}
    existing_urls: set = set()
    if output.get("merge_existing"):
        existing_by_year, existing_urls = load_existing_by_year(output_dir)
        if existing_urls:
            print(f"Found {len(existing_urls)} existing records to skip")

    extractor = get_extractor(source_name)
    driver = setup_selenium_driver(cfg)

    all_records: List[dict] = [
        record for records in existing_by_year.values() for record in records
    ]
    should_stop = False
    current_page = 1
    new_count = 0
    skipped_count = 0

    try:
        print(f"Loading {cfg['target_url']}...")
        driver.get(cfg["target_url"])
        WebDriverWait(driver, int(cfg.get("selenium", {}).get("wait_seconds", 20))).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(5)

        total_pages = get_total_pages(driver, total_pages_selector)
        print(f"Found {total_pages} pages to scrape")

        while current_page <= total_pages and not should_stop:
            print(f"Scraping page {current_page}/{total_pages}...")
            time.sleep(2)

            page_records = extractor(driver.page_source, cfg)

            for record in page_records:
                record_url = record.get("url")
                year = extract_year_from_date(record.get("date"))

                if year and year < min_year:
                    print(f"Reached year {year} (below minimum {min_year}), stopping extraction.")
                    should_stop = True
                    break

                if record_url in existing_urls:
                    skipped_count += 1
                    continue

                all_records.append(record)
                existing_urls.add(record_url)
                new_count += 1

            if should_stop:
                break

            if current_page < total_pages:
                if click_next_page(driver, next_button_selector):
                    time.sleep(3)
                    current_page += 1
                else:
                    print("Could not find next page button, stopping.")
                    break
            else:
                break
    finally:
        driver.quit()

    print(f"Extracted {new_count} new records, skipped {skipped_count} existing")
    return all_records


# ----------------------------
# Output
# ----------------------------

def year_of(record: dict) -> str:
    date_str = record.get("date") or record.get("published_date") or ""
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
        print(f"  Saved {len(items)} records to {year}.json")


def save_output(records: List[dict], cfg: Dict[str, Any], overrides) -> None:
    output = cfg["output"]

    if output.get("group_by_year"):
        output_dir = resolve_path(output["dir"], CONFIG_DIR)
        save_by_year(records, output_dir)
        years = sorted({year_of(r) for r in records})
        print(f"Saved {len(records)} total records to {output_dir} (years: {', '.join(years)})")
        return

    output_dir = resolve_path(output["dir"], CONFIG_DIR)
    filename = overrides.output_file or output.get("filename", "output.json")
    output_path = output_dir / filename
    save_json(records, output_path)
    print(f"Saved {len(records)} records to {output_path}")


# ----------------------------
# Engine
# ----------------------------

PAGINATION_MODES = {
    "none": run_single_page,
    "url_param": run_url_param_pagination,
    "url_path": run_url_path_pagination,
    "letters": run_letters_pagination,
    "search": run_search_pagination,
    "selenium_click": run_selenium_click_pagination,
}


def run_source(source_name: str, cfg: Dict[str, Any], overrides) -> None:
    pagination = cfg.get("pagination", {})
    mode = pagination.get("mode", "none")

    if mode not in PAGINATION_MODES:
        raise ValueError(f"Unknown pagination mode '{mode}' for source '{source_name}'")

    records = PAGINATION_MODES[mode](source_name, cfg, overrides)
    save_output(records, cfg, overrides)


# ----------------------------
# CLI
# ----------------------------

def apply_overrides(cfg: Dict[str, Any], args) -> Dict[str, Any]:
    if args.method:
        cfg["method"] = args.method

    if args.output_dir:
        cfg.setdefault("output", {})["dir"] = args.output_dir
    if args.output_file:
        cfg.setdefault("output", {})["filename"] = args.output_file

    request_cfg = cfg.setdefault("request", {})
    if args.request_delay is not None:
        request_cfg["delay"] = args.request_delay
    if args.request_timeout is not None:
        request_cfg["timeout"] = args.request_timeout
    if args.user_agent:
        request_cfg["user_agent"] = args.user_agent

    selenium_cfg = cfg.setdefault("selenium", {})
    if args.headed:
        selenium_cfg["headless"] = False

    pagination = cfg.setdefault("pagination", {})
    if args.start_page is not None:
        pagination["start_page"] = args.start_page
    if args.end_page is not None:
        pagination["end_page"] = args.end_page
    if args.max_pages is not None:
        pagination["max_pages"] = args.max_pages
    if args.min_year is not None:
        pagination["min_year"] = args.min_year
    if args.max_pages_per_keyword is not None:
        pagination["max_pages_per_keyword"] = args.max_pages_per_keyword
    if args.num_processes is not None:
        pagination["num_processes"] = args.num_processes

    filters = cfg.setdefault("filters", {})
    if args.max_age_days is not None:
        filters["max_age_days"] = args.max_age_days

    return cfg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified list scraper for all sources (driven by config.py)"
    )
    source_group = parser.add_mutually_exclusive_group(required=False)
    source_group.add_argument("--source", help="Source name to scrape (see --list)")
    source_group.add_argument("--all", action="store_true", help="Run all sources in config order")
    parser.add_argument("--list", action="store_true", help="List available source names and exit")

    parser.add_argument("--method", choices=["bs4", "selenium", "auto", "playwright"],
                        help="Override the fetch method")
    parser.add_argument("--start-page", type=int, help="Page number to start from")
    parser.add_argument("--end-page", type=int, help="Last page number (inclusive)")
    parser.add_argument("--max-pages", type=int, help="Maximum number of pages to scrape")
    parser.add_argument("--min-year", type=int, help="Stop when records older than this year are reached")
    parser.add_argument("--max-age-days", type=int, help="Only keep records newer than N days")
    parser.add_argument("--letters", help="Letters to scrape (e.g. 'a b c' or 'all')")
    parser.add_argument("--max-pages-per-keyword", type=int, help="Search mode: pages per keyword")
    parser.add_argument("--num-processes", type=int, help="Search mode: number of worker processes")
    parser.add_argument("--output-dir", help="Override output directory")
    parser.add_argument("--output-file", help="Override output filename")
    parser.add_argument("--request-delay", type=float, help="Delay between requests (seconds)")
    parser.add_argument("--request-timeout", type=int, help="HTTP timeout (seconds)")
    parser.add_argument("--user-agent", help="HTTP User-Agent string")
    parser.add_argument("--headed", action="store_true", help="Run Selenium in headed mode")
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

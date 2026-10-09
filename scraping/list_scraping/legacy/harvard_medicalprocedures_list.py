#!/usr/bin/env python3
"""
Scrape Harvard Health A-to-Z topics list.

Target page:
    https://www.health.harvard.edu/diagnostic-tests-and-medical-procedures

Output JSON item schema:
    {
      "title": "...",
      "url": "...",
      "category": "Medical Procedures",
      "scraped_at": "2026-04-14T12:34:56Z",
      "tags": ["Harvard Health Publishing", "Diagnostic Tests and Medical Procedures"]
    }

Default scraper method is BS4 (faster for static pages).
Optional Selenium method is available as fallback.

Dependencies:
    pip install requests beautifulsoup4

Optional Selenium dependencies:
    pip install selenium webdriver-manager
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ----------------------------
# Configurable defaults
# ----------------------------
BASE_URL = "https://www.health.harvard.edu"
TARGET_URL = f"{BASE_URL}/diagnostic-tests-and-medical-procedures"

DEFAULT_CATEGORY = "Medical Procedures"
DEFAULT_TAGS = ["Harvard Health Publishing", "Diagnostic Tests and Medical Procedures"]

# Output defaults (resolved relative to this script directory)
DEFAULT_OUTPUT_DIR = "../../../storage/harvardhealth"
DEFAULT_OUTPUT_FILENAME = "harvard_medical_procedures_list.json"

# Network + parsing behavior
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_REQUEST_DELAY = 0.1
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; HarvardHealthTopicsScraper/1.0)"

# Selenium behavior
DEFAULT_SELENIUM_WAIT_SECONDS = 15
DEFAULT_SELENIUM_HEADLESS = True

# Method choices: bs4 | selenium | auto
DEFAULT_METHOD = "bs4"

# Primary container from the page markup shared by user:
# <div class="mt-6"><ul class="list-none ...">...</ul></div>
PRIMARY_LIST_SELECTOR = "div.mt-6 > ul.list-none"

# Fallback selectors in case Harvard changes class names.
TOPIC_SELECTORS = [
    "main div.mt-6 > ul > li > a",
    "article div.mt-6 > ul > li > a",
    "main div > ul > li > a",
    "article div > ul > li > a",
]


@dataclass
class ScrapeConfig:
    target_url: str
    output_dir: Path
    output_filename: str
    category: str
    tags: List[str]
    request_timeout: int
    request_delay: float
    user_agent: str
    method: str
    selenium_wait_seconds: int
    selenium_headless: bool


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_valid_topic_link(href: str) -> bool:
    if not href:
        return False
    absolute = urljoin(BASE_URL, href)
    parsed = urlparse(absolute)
    if parsed.netloc != "www.health.harvard.edu":
        return False
    if not parsed.path:
        return False
    return True


def dedupe_records(records: Iterable[dict]) -> List[dict]:
    seen = set()
    out = []
    for row in records:
        key = row.get("url")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def build_records_from_html(html: str, category: str, tags: List[str], scraped_at: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records: List[dict] = []

    # First pass: strict extraction from the known list container.
    primary_container = soup.select_one(PRIMARY_LIST_SELECTOR)
    if primary_container:
        for a in primary_container.select("li > a"):
            href = (a.get("href") or "").strip()
            title = a.get_text(" ", strip=True)
            if not title or not is_valid_topic_link(href):
                continue

            records.append(
                {
                    "title": title,
                    "url": urljoin(BASE_URL, href),
                    "category": category,
                    "scraped_at": scraped_at,
                    "tags": tags,
                }
            )

    # Fallback: broader selectors, but only if strict mode found nothing.
    if records:
        return dedupe_records(records)

    for selector in TOPIC_SELECTORS:
        for a in soup.select(selector):
            href = (a.get("href") or "").strip()
            title = a.get_text(" ", strip=True)
            if not title or not is_valid_topic_link(href):
                continue

            records.append(
                {
                    "title": title,
                    "url": urljoin(BASE_URL, href),
                    "category": category,
                    "scraped_at": scraped_at,
                    "tags": tags,
                }
            )

    return dedupe_records(records)


def fetch_with_bs4(config: ScrapeConfig) -> List[dict]:
    headers = {"User-Agent": config.user_agent}
    response = requests.get(config.target_url, headers=headers, timeout=config.request_timeout)
    response.raise_for_status()
    if config.request_delay > 0:
        time.sleep(config.request_delay)
    return build_records_from_html(
        html=response.text,
        category=config.category,
        tags=config.tags,
        scraped_at=utc_now_iso(),
    )


def fetch_with_selenium(config: ScrapeConfig) -> List[dict]:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError as exc:
        raise RuntimeError(
            "Selenium mode requires: pip install selenium webdriver-manager"
        ) from exc

    options = Options()
    if config.selenium_headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument(f"--user-agent={config.user_agent}")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    try:
        driver.get(config.target_url)
        WebDriverWait(driver, config.selenium_wait_seconds).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div > ul > li > a"))
        )
        if config.request_delay > 0:
            time.sleep(config.request_delay)
        html = driver.page_source
    finally:
        driver.quit()

    return build_records_from_html(
        html=html,
        category=config.category,
        tags=config.tags,
        scraped_at=utc_now_iso(),
    )


def scrape_topics(config: ScrapeConfig) -> List[dict]:
    method = config.method.lower().strip()

    if method == "bs4":
        return fetch_with_bs4(config)
    if method == "selenium":
        return fetch_with_selenium(config)
    if method == "auto":
        records = fetch_with_bs4(config)
        if records:
            return records
        return fetch_with_selenium(config)

    raise ValueError("Invalid method. Use one of: bs4, selenium, auto")


def save_json(records: List[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def build_config_from_args() -> ScrapeConfig:
    parser = argparse.ArgumentParser(description="Harvard Health Medical Procedures scraper")
    parser.add_argument("--url", default=TARGET_URL, help="Target URL to scrape.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory for JSON file.")
    parser.add_argument("--output-file", default=DEFAULT_OUTPUT_FILENAME, help="Output JSON filename.")
    parser.add_argument("--category", default=DEFAULT_CATEGORY, help="Category value for each record.")
    parser.add_argument(
        "--tags",
        default=",".join(DEFAULT_TAGS),
        help="Comma-separated tags. Example: 'Harvard Health Publishing,Health Topics'",
    )
    parser.add_argument("--request-timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT, help="HTTP timeout in seconds.")
    parser.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY, help="Delay after fetch in seconds.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent string.")
    parser.add_argument(
        "--method",
        default=DEFAULT_METHOD,
        choices=["bs4", "selenium", "auto"],
        help="Scraper method: bs4 (fast), selenium, or auto (bs4 then selenium fallback).",
    )
    parser.add_argument(
        "--selenium-wait-seconds",
        type=int,
        default=DEFAULT_SELENIUM_WAIT_SECONDS,
        help="Seconds to wait for topic links in selenium mode.",
    )
    parser.add_argument(
        "--selenium-headed",
        action="store_true",
        help="Run Selenium in headed mode (default is headless).",
    )

    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    output_dir = (script_dir / args.output_dir).resolve()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    return ScrapeConfig(
        target_url=args.url,
        output_dir=output_dir,
        output_filename=args.output_file,
        category=args.category,
        tags=tags,
        request_timeout=args.request_timeout,
        request_delay=args.request_delay,
        user_agent=args.user_agent,
        method=args.method,
        selenium_wait_seconds=args.selenium_wait_seconds,
        selenium_headless=not args.selenium_headed,
    )


def main() -> None:
    config = build_config_from_args()
    output_path = config.output_dir / config.output_filename

    records = scrape_topics(config)
    save_json(records, output_path)

    print(f"Saved {len(records)} medical procedures to {output_path}")


if __name__ == "__main__":
    main()

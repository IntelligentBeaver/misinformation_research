#!/usr/bin/env python3
"""
Shared utilities for the unified scraping framework.

Used by:
- list_scraping/scrape_all.py (headline/index scraping)
- detail_scraping/scrape_all.py (article detail scraping)
- preprocessing/ scripts

Provides:
    - HTTP fetching (requests with retries, Selenium fallback, Playwright support)
    - Date normalization / parsing helpers (UTC ISO, YYYY-MM-DD, year extraction)
    - Record deduplication (by URL)
    - JSON load/save helpers (with encoding handling)
    - Path resolution relative to config file location
    - Text normalization (whitespace, slugs)

Dependencies:
    pip install requests beautifulsoup4 lxml

Optional Selenium dependencies:
    pip install selenium webdriver-manager

Optional Playwright dependencies:
    pip install playwright && playwright install chromium
"""

from __future__ import annotations

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CONFIG_DIR = Path(__file__).resolve().parent

DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_REQUEST_DELAY = 0.1
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; ResearchScraper/1.0)"


# ----------------------------
# Time helpers
# ----------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ----------------------------
# Text helpers
# ----------------------------

def normalize_space(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def make_slug(url: str, fallback: str = "article") -> str:
    path = urlparse(url).path
    slug = path.rstrip("/").split("/")[-1].strip().lower()
    slug = re.sub(r"[^a-z0-9\-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or fallback


# ----------------------------
# Date helpers
# ----------------------------

def normalize_date(raw: Optional[str]) -> str:
    """Normalize a date string to YYYY-MM-DD when possible."""
    text = normalize_space(raw)
    if not text:
        return ""

    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)

    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return text


def parse_date_str(value: Optional[str]) -> Optional[str]:
    """Parse common date formats to ISO date; returns the input as-is if unparseable."""
    if not value:
        return None
    text = value.strip()
    formats = [
        "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y",
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def extract_year_from_date(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    match = re.search(r"\b(19|20)\d{2}\b", str(date_str))
    if match:
        return int(match.group(0))
    return None


# ----------------------------
# Record helpers
# ----------------------------

def dedupe_records(records: Iterable[dict]) -> List[dict]:
    seen: set = set()
    out: List[dict] = []
    for row in records:
        key = row.get("url")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def urls_from_records(records: Iterable[dict]) -> set:
    return {row.get("url") for row in records if row.get("url")}


# ----------------------------
# JSON IO
# ----------------------------

def save_json(data: Any, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def load_json_list(path: Path) -> List[dict]:
    """Load a JSON file that is either a list of records or a dict containing one."""
    data = load_json(path)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return [row for row in data["items"] if isinstance(row, dict)]
        for value in data.values():
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


# ----------------------------
# Path resolution
# ----------------------------

def resolve_path(path: str, base_dir: Optional[Path] = None) -> Path:
    """Resolve a config path relative to the config file directory (or base_dir)."""
    base = Path(base_dir) if base_dir else CONFIG_DIR
    return (base / path).resolve()


# ----------------------------
# HTTP fetching
# ----------------------------

def requests_session_with_retries(
    user_agent: str = DEFAULT_USER_AGENT,
    total_retries: int = 3,
    backoff_factor: float = 0.5,
) -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.headers.update({"User-Agent": user_agent})
    return session


def request_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("request", {})


def fetch_html_bs4(url: str, cfg: Dict[str, Any], session: Optional[requests.Session] = None) -> str:
    """Fetch a page with requests and return the HTML text."""
    req = request_settings(cfg)
    headers = {"User-Agent": req.get("user_agent", DEFAULT_USER_AGENT)}
    timeout = int(req.get("timeout", DEFAULT_REQUEST_TIMEOUT))

    owns_session = False
    if session is None:
        session = requests.Session()
        session.headers.update(headers)
        owns_session = True

    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        return response.text
    finally:
        delay = float(req.get("delay", 0) or 0)
        if delay > 0:
            time.sleep(delay)
        if owns_session:
            session.close()


def setup_selenium_driver(cfg: Dict[str, Any]) -> Any:
    """Create a headless Chrome driver (falls back to Edge)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    req = request_settings(cfg)
    selenium_cfg = cfg.get("selenium", {})
    user_agent = req.get("user_agent", DEFAULT_USER_AGENT)
    headless = bool(selenium_cfg.get("headless", True))

    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(f"--user-agent={user_agent}")

    try:
        return webdriver.Chrome(options=chrome_options)
    except Exception as exc:
        print(f"Chrome not available: {exc}")
        print("Trying Edge...")
        edge_options = webdriver.EdgeOptions()
        if headless:
            edge_options.add_argument("--headless")
        edge_options.add_argument("--no-sandbox")
        edge_options.add_argument("--disable-dev-shm-usage")
        edge_options.add_argument(f"--user-agent={user_agent}")
        return webdriver.Edge(options=edge_options)


def fetch_html_selenium(url: str, cfg: Dict[str, Any]) -> str:
    """Fetch a page with Selenium and return the rendered HTML text."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    selenium_cfg = cfg.get("selenium", {})
    wait_seconds = int(selenium_cfg.get("wait_seconds", 15))
    wait_selector = selenium_cfg.get("wait_selector", "body")

    driver = setup_selenium_driver(cfg)
    try:
        driver.get(url)
        WebDriverWait(driver, wait_seconds).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
        )
        delay = float(request_settings(cfg).get("delay", 0) or 0)
        if delay > 0:
            time.sleep(delay)
        return driver.page_source
    finally:
        driver.quit()


def fetch_html(url: str, cfg: Dict[str, Any], method: str = "bs4") -> str:
    """Fetch a page using the configured method (bs4 | selenium)."""
    method = (method or "bs4").lower().strip()
    if method == "bs4":
        return fetch_html_bs4(url, cfg)
    if method == "selenium":
        return fetch_html_selenium(url, cfg)
    raise ValueError(f"Invalid method: {method}. Use one of: bs4, selenium")


def fetch_and_extract(
    url: str,
    cfg: Dict[str, Any],
    extractor,
    method: str = "bs4",
    session: Optional[requests.Session] = None,
) -> List[dict]:
    """Fetch a page and run the extractor. 'auto' tries bs4 first, then selenium."""
    method = (method or "bs4").lower().strip()
    if method == "auto":
        html = fetch_html_bs4(url, cfg, session=session)
        records = extractor(html, cfg)
        if records:
            return records
        return extractor(fetch_html_selenium(url, cfg), cfg)
    html = fetch_html(url, cfg, method=method)
    return extractor(html, cfg)

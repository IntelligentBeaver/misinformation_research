#!/usr/bin/env python3
"""
Scrape Harvard Health blog list pages.

Target page pattern:
	https://www.health.harvard.edu/blog?page=1

Output JSON item schema:
	{
	  "title": "...",
	  "url": "...",
	  "category": "Blog",
	  "scraped_at": "2026-04-14T12:34:56Z",
	  "tags": ["Harvard Health Publishing", "Blog"]
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
from typing import Iterable, List, Optional
from urllib.parse import urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ----------------------------
# Configurable defaults
# ----------------------------
BASE_URL = "https://www.health.harvard.edu"
BLOG_PATH = "/blog"
TARGET_URL = f"{BASE_URL}{BLOG_PATH}"

DEFAULT_CATEGORY = "Blog"
DEFAULT_TAGS = ["Harvard Health Publishing", "Blog"]

DEFAULT_OUTPUT_DIR = "../../../storage/harvardhealth"
DEFAULT_OUTPUT_FILENAME = "harvard_blogs_list.json"

DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_REQUEST_DELAY = 0.1
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; HarvardHealthBlogsScraper/1.0)"

DEFAULT_METHOD = "bs4"
DEFAULT_START_PAGE = 1
DEFAULT_MAX_PAGES = 150
DEFAULT_STOP_ON_EXISTING_DUPLICATE = True

DEFAULT_SELENIUM_WAIT_SECONDS = 15
DEFAULT_SELENIUM_HEADLESS = True

ARTICLE_LIST_SELECTOR = 'div[data-cypress="article-list"]'
ARTICLE_CARD_SELECTOR = 'div[data-cypress="article-list"] > div'
ARTICLE_LINK_SELECTOR = 'a[data-cypress="article-link"]'


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
	start_page: int
	end_page: Optional[int]
	max_pages: Optional[int]
	stop_on_empty_page: bool
	stop_on_existing_duplicate: bool
	selenium_wait_seconds: int
	selenium_headless: bool


def utc_now_iso() -> str:
	return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def page_url(base_url: str, page: int) -> str:
	return f"{base_url}?{urlencode({'page': page})}"


def is_valid_blog_link(href: str) -> bool:
	if not href:
		return False
	absolute = urljoin(BASE_URL, href)
	parsed = urlparse(absolute)
	if parsed.netloc != "www.health.harvard.edu":
		return False
	if not parsed.path.startswith("/blog/"):
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


def load_existing_records(output_path: Path) -> List[dict]:
	if not output_path.exists():
		return []

	try:
		with output_path.open("r", encoding="utf-8") as f:
			data = json.load(f)
		if not isinstance(data, list):
			return []
		return [item for item in data if isinstance(item, dict) and item.get("url")]
	except (json.JSONDecodeError, OSError):
		return []


def urls_from_records(records: Iterable[dict]) -> set:
	return {row.get("url") for row in records if row.get("url")}


def parse_page_html(html: str, config: ScrapeConfig) -> List[dict]:
	soup = BeautifulSoup(html, "html.parser")
	records: List[dict] = []

	cards = soup.select(ARTICLE_CARD_SELECTOR)
	if not cards:
		container = soup.select_one(ARTICLE_LIST_SELECTOR)
		if not container:
			return records
		cards = container.find_all("div", recursive=False)

	scraped_at = utc_now_iso()

	for card in cards:
		link = card.select_one(ARTICLE_LINK_SELECTOR)
		if not link:
			continue

		href = (link.get("href") or "").strip()
		if not is_valid_blog_link(href):
			continue

		title_node = card.select_one("h2")
		if title_node:
			title = title_node.get_text(" ", strip=True)
		else:
			# Fallback: grab text from screen-reader span if h2 is unavailable.
			title = link.get_text(" ", strip=True).replace("Read More about", "").strip()

		if not title:
			continue

		records.append(
			{
				"title": title,
				"url": urljoin(BASE_URL, href),
				"category": config.category,
				"scraped_at": scraped_at,
				"tags": config.tags,
			}
		)

	return dedupe_records(records)


def fetch_page_bs4(config: ScrapeConfig, page: int) -> List[dict]:
	headers = {"User-Agent": config.user_agent}
	url = page_url(config.target_url, page)
	response = requests.get(url, headers=headers, timeout=config.request_timeout)
	if response.status_code == 404:
		# No more paginated pages.
		return []
	response.raise_for_status()
	if config.request_delay > 0:
		time.sleep(config.request_delay)
	return parse_page_html(response.text, config)


def fetch_page_selenium(config: ScrapeConfig, page: int) -> List[dict]:
	try:
		from selenium import webdriver
		from selenium.webdriver.chrome.options import Options
		from selenium.webdriver.chrome.service import Service
		from selenium.webdriver.common.by import By
		from selenium.webdriver.support import expected_conditions as EC
		from selenium.webdriver.support.ui import WebDriverWait
		from webdriver_manager.chrome import ChromeDriverManager
	except ImportError as exc:
		raise RuntimeError("Selenium mode requires: pip install selenium webdriver-manager") from exc

	options = Options()
	if config.selenium_headless:
		options.add_argument("--headless=new")
	options.add_argument("--disable-gpu")
	options.add_argument("--no-sandbox")
	options.add_argument(f"--user-agent={config.user_agent}")

	driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
	try:
		driver.get(page_url(config.target_url, page))
		WebDriverWait(driver, config.selenium_wait_seconds).until(
			EC.presence_of_element_located((By.CSS_SELECTOR, ARTICLE_LIST_SELECTOR))
		)
		if config.request_delay > 0:
			time.sleep(config.request_delay)
		html = driver.page_source
	finally:
		driver.quit()

	return parse_page_html(html, config)


def fetch_page(config: ScrapeConfig, page: int) -> List[dict]:
	method = config.method.lower().strip()
	if method == "bs4":
		return fetch_page_bs4(config, page)
	if method == "selenium":
		return fetch_page_selenium(config, page)
	if method == "auto":
		data = fetch_page_bs4(config, page)
		if data:
			return data
		return fetch_page_selenium(config, page)
	raise ValueError("Invalid method. Use one of: bs4, selenium, auto")


def scrape_pages(config: ScrapeConfig, existing_urls: Optional[set] = None) -> List[dict]:
	all_records: List[dict] = []
	seen_in_run: set = set()
	existing_urls = existing_urls or set()
	page = config.start_page
	pages_scraped = 0

	while True:
		if config.end_page is not None and page > config.end_page:
			break
		if config.max_pages is not None and pages_scraped >= config.max_pages:
			break

		page_records = fetch_page(config, page)
		if not page_records and config.stop_on_empty_page:
			break

		duplicate_found = False
		for record in page_records:
			url = record.get("url")
			if not url:
				continue

			if url in seen_in_run:
				continue

			if url in existing_urls:
				duplicate_found = True
				continue

			seen_in_run.add(url)
			all_records.append(record)
			print(f"Extracted: {record.get('title', '')}")

		if config.stop_on_existing_duplicate and duplicate_found:
			break

		page += 1
		pages_scraped += 1

	return dedupe_records(all_records)


def save_json(records: List[dict], output_path: Path) -> None:
	output_path.parent.mkdir(parents=True, exist_ok=True)
	with output_path.open("w", encoding="utf-8") as f:
		json.dump(records, f, ensure_ascii=False, indent=2)


def build_config_from_args() -> ScrapeConfig:
	parser = argparse.ArgumentParser(description="Harvard Health blog list scraper")
	parser.add_argument("--url", default=TARGET_URL, help="Target list URL without page param.")
	parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory for JSON file.")
	parser.add_argument("--output-file", default=DEFAULT_OUTPUT_FILENAME, help="Output JSON filename.")
	parser.add_argument("--category", default=DEFAULT_CATEGORY, help="Category value for each record.")
	parser.add_argument(
		"--tags",
		default=",".join(DEFAULT_TAGS),
		help="Comma-separated tags. Example: Harvard Health Publishing,Blog",
	)
	parser.add_argument("--request-timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT, help="HTTP timeout in seconds.")
	parser.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY, help="Delay between requests in seconds.")
	parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent string.")
	parser.add_argument(
		"--method",
		default=DEFAULT_METHOD,
		choices=["bs4", "selenium", "auto"],
		help="Scraper method: bs4, selenium, or auto (bs4 then selenium fallback).",
	)

	parser.add_argument("--start-page", type=int, default=DEFAULT_START_PAGE, help="Page number to start from.")
	parser.add_argument("--end-page", type=int, default=None, help="Last page number to scrape, inclusive.")
	parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Maximum number of pages to scrape.")
	parser.add_argument(
		"--continue-on-empty",
		action="store_true",
		help="Continue even if a page has no blog cards.",
	)
	parser.add_argument(
		"--no-stop-on-existing-duplicate",
		action="store_true",
		help="Do not stop paging when a blog URL already exists in output JSON.",
	)

	parser.add_argument(
		"--selenium-wait-seconds",
		type=int,
		default=DEFAULT_SELENIUM_WAIT_SECONDS,
		help="Seconds to wait for blog list in selenium mode.",
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
		start_page=args.start_page,
		end_page=args.end_page,
		max_pages=args.max_pages,
		stop_on_empty_page=not args.continue_on_empty,
		stop_on_existing_duplicate=not args.no_stop_on_existing_duplicate,
		selenium_wait_seconds=args.selenium_wait_seconds,
		selenium_headless=not args.selenium_headed,
	)


def main() -> None:
	config = build_config_from_args()
	output_path = config.output_dir / config.output_filename
	existing_records = load_existing_records(output_path)
	existing_urls = urls_from_records(existing_records)

	new_records = scrape_pages(config, existing_urls=existing_urls)
	merged_records = dedupe_records([*existing_records, *new_records])
	save_json(merged_records, output_path)

	print(
		f"Saved {len(merged_records)} total blog records to {output_path} "
		f"(added {len(new_records)} new records)"
	)


if __name__ == "__main__":
	main()


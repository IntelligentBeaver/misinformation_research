#!/usr/bin/env python3
"""
Extract Harvard Health search results for a query and then scrape each matching article
into a compact article-detail JSON containing only the title and sections.

Output layout:
    benchmark/
      harvard_search_results_{query}.json
      articles/
        {slug}.json

Example:
    python harvard_search_articles.py --query diet --max-pages 2
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from playwright.sync_api import sync_playwright


BASE_URL = "https://www.health.harvard.edu"
SCRAPING_ROOT = Path(__file__).resolve().parent
ARTICLES_DIR = SCRAPING_ROOT / "articles"

QUERY = "myth"
OUTPUT_DIR = SCRAPING_ROOT
ARTICLES_OUTPUT_DIR = ARTICLES_DIR
START_PAGE = 1
MAX_PAGES = 5
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.2
USER_AGENT = "Mozilla/5.0 (compatible; HarvardSearchScraper/1.0)"
CONTINUE_ON_ERROR = True

# Backwards-compatible aliases
DEFAULT_REQUEST_TIMEOUT = REQUEST_TIMEOUT
DEFAULT_REQUEST_DELAY = REQUEST_DELAY
DEFAULT_USER_AGENT = USER_AGENT
DEFAULT_START_PAGE = START_PAGE
DEFAULT_MAX_PAGES = MAX_PAGES


@dataclass
class ScrapeConfig:
    query: str
    output_dir: Path
    articles_dir: Path
    start_page: int
    max_pages: Optional[int]
    request_timeout: int
    request_delay: float
    user_agent: str
    continue_on_error: bool


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_space(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def extract_text(node: Optional[Tag]) -> str:
    if not node:
        return ""
    return normalize_space(node.get_text(" ", strip=True))


def dedupe_keep_order(values: Iterable[Optional[str]]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        if not value:
            continue
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def make_slug(url: str) -> str:
    path = urlparse(url).path
    slug = path.rstrip("/").split("/")[-1].strip().lower()
    slug = re.sub(r"[^a-z0-9\-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "article"


def result_search_url(query: str, page: int) -> str:
    encoded = quote(query.strip())
    return f"{BASE_URL}/search?content%5Bquery%5D={encoded}&content%5Bpage%5D={page}"


def is_valid_article_url(href: str) -> bool:
    if not href:
        return False
    candidate = href if href.startswith("http") else urljoin(BASE_URL, href)
    parsed = urlparse(candidate)
    if parsed.netloc not in {"www.health.harvard.edu", "health.harvard.edu"}:
        return False
    if parsed.path in {"/", "/search", ""}:
        return False
    return True


def find_result_links(soup: BeautifulSoup) -> List[dict]:
    records: List[dict] = []
    seen = set()

    for link in soup.select("main h3 a[href]"):
        href = (link.get("href") or "").strip()
        if not href or not is_valid_article_url(href):
            continue

        title = extract_text(link)
        if not title or "read the article" in title.lower():
            continue

        full_url = href if href.startswith("http") else urljoin(BASE_URL, href)
        if full_url in seen:
            continue
        seen.add(full_url)

        records.append({
            "title": title,
            "url": full_url,
            "category": "",
            "query": "",
        })

    return records


def fetch_search_results(session: requests.Session, query: str, page: int, timeout: int) -> List[dict]:
    url = result_search_url(query, page)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    records = find_result_links(soup)
    for record in records:
        record["query"] = query
    return records


def fetch_search_results_playwright(query: str, page: int, timeout_ms: int = 60000) -> List[dict]:
    url = result_search_url(query, page)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page_obj = browser.new_page()
        page_obj.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page_obj.wait_for_timeout(1500)

        selectors = [
            "main h3 a[href]",
            "#hits a[href]",
            "main a[href]",
            "a[href]",
        ]

        raw = []
        for selector in selectors:
            try:
                candidate = page_obj.locator(selector).evaluate_all(
                    "els => els.map(el => ({href: el.href, text: (el.textContent || '').trim()}))"
                )
                if candidate:
                    raw = candidate
                    break
            except Exception:
                continue

        browser.close()

    records: List[dict] = []
    seen = set()
    for item in raw:
        href = (item or {}).get("href", "").strip()
        title = (item or {}).get("text", "").strip()
        if not href or not title:
            continue
        if not is_valid_article_url(href):
            continue
        if "read the article" in title.lower():
            continue
        if href in seen:
            continue
        seen.add(href)
        records.append({
            "title": title,
            "url": href,
            "category": "",
            "query": query,
        })
    return records


def collect_search_results(config: ScrapeConfig, session: requests.Session) -> List[dict]:
    all_results: List[dict] = []
    seen_urls = set()
    page = config.start_page
    pages_processed = 0

    while True:
        if config.max_pages is not None and pages_processed >= config.max_pages:
            break

        page_records = fetch_search_results_playwright(config.query, page, timeout_ms=config.request_timeout * 1000)
        if not page_records:
            break

        for record in page_records:
            url = record.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            all_results.append(record)

        pages_processed += 1
        page += 1

        if config.request_delay > 0:
            time.sleep(config.request_delay)

    return all_results


def node_is_under_content_container(node: Tag, container: Tag) -> bool:
    parent = node.parent
    while isinstance(parent, Tag):
        if parent is container:
            return True
        parent = parent.parent
    return False


def extract_list_items(list_node: Tag) -> List[str]:
    items: List[str] = []
    for li in list_node.find_all("li", recursive=False):
        text = extract_text(li)
        if text:
            items.append(text)
    return dedupe_keep_order(items)


def build_sections(content_root: Optional[Tag]) -> List[Dict]:
    if not content_root:
        return []

    sections: List[Dict] = []
    current_section: Dict = {"heading": None, "content_blocks": []}
    relevant_tags = {"h2", "h3", "h4", "p", "ul", "ol"}

    for node in content_root.descendants:
        if not isinstance(node, Tag):
            continue
        if node.name not in relevant_tags:
            continue
        if not node_is_under_content_container(node, content_root):
            continue
        if node.find_parent(["script", "style", "noscript", "figure", "table"]):
            continue

        if node.name in {"h2", "h3", "h4"}:
            heading_text = extract_text(node)
            if not heading_text:
                continue
            if current_section["heading"] is not None or current_section["content_blocks"]:
                sections.append(current_section)
            current_section = {"heading": heading_text, "content_blocks": []}
            continue

        if node.name == "p":
            if node.find("img") or node.find_parent("li"):
                continue
            paragraph_text = extract_text(node)
            if not paragraph_text:
                continue
            current_section["content_blocks"].append({
                "type": "paragraph",
                "text": paragraph_text,
                "associated_bullets": None,
            })
            continue

        if node.name in {"ul", "ol"}:
            if node.find_parent(["ul", "ol"]):
                continue
            items = extract_list_items(node)
            if not items:
                continue
            if current_section["content_blocks"]:
                last_block = current_section["content_blocks"][-1]
                if last_block.get("type") == "paragraph":
                    existing = last_block.get("associated_bullets") or []
                    last_block["associated_bullets"] = dedupe_keep_order(existing + items)
                    continue
            current_section["content_blocks"].append({
                "type": "paragraph",
                "text": None,
                "associated_bullets": items,
            })

    if current_section["heading"] is not None or current_section["content_blocks"]:
        sections.append(current_section)

    cleaned_sections: List[Dict] = []
    for sec in sections:
        content_blocks = sec.get("content_blocks") or []
        if not content_blocks and sec.get("heading") is None:
            continue
        cleaned_sections.append({
            "heading": sec.get("heading"),
            "content_blocks": content_blocks,
        })
    return cleaned_sections


def extract_article_title_and_sections(item: dict, html: str, final_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    article_scope = soup.select_one("article")
    header = (article_scope.select_one("header") if article_scope else None) or soup.select_one("article header") or soup.select_one("header")
    content_root = soup.select_one("div.content-repository-content")

    title = extract_text((header.find("h1") if header else None) or soup.find("h1"))
    if not title and item.get("title"):
        title = normalize_space(str(item.get("title")))

    return {
        "url": final_url,
        "title": title,
        "slug": item.get("slug") or make_slug(final_url),
        "sections": build_sections(content_root),
    }


def save_json(data, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_config() -> ScrapeConfig:
    return ScrapeConfig(
        query=QUERY.strip(),
        output_dir=Path(OUTPUT_DIR).resolve(),
        articles_dir=Path(ARTICLES_OUTPUT_DIR).resolve(),
        start_page=max(1, START_PAGE),
        max_pages=MAX_PAGES,
        request_timeout=REQUEST_TIMEOUT,
        request_delay=REQUEST_DELAY,
        user_agent=USER_AGENT,
        continue_on_error=CONTINUE_ON_ERROR,
    )


def main() -> None:
    config = build_config()
    output_path = config.output_dir / f"harvard_search_results_{re.sub(r'[^a-z0-9]+', '_', config.query.lower()).strip('_') or 'all'}.json"
    config.articles_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})

    results = collect_search_results(config, session)
    save_json(results, output_path)
    print(f"Saved search results list: {output_path} ({len(results)} articles)")

    for idx, item in enumerate(results, start=1):
        url = item.get("url", "").strip()
        if not url:
            continue

        slug = make_slug(url)
        article_path = config.articles_dir / f"{slug}.json"
        if article_path.exists():
            print(f"[{idx}/{len(results)}] Skipping existing article detail: {slug}")
            continue

        try:
            response = session.get(url, timeout=config.request_timeout)
            response.raise_for_status()
            article = extract_article_title_and_sections(item, response.text, response.url)
            save_json(article, article_path)
            print(f"[{idx}/{len(results)}] Saved article detail: {article_path.name}")
        except Exception as exc:
            error_payload = {
                "url": url,
                "slug": slug,
                "error": str(exc),
                "scrape_timestamp_utc": utc_now_iso(),
            }
            save_json(error_payload, config.articles_dir / f"{slug}.error.json")
            if config.continue_on_error:
                print(f"[{idx}/{len(results)}] Error scraping {slug}: {exc}")
                continue
            raise

        if config.request_delay > 0:
            time.sleep(config.request_delay)

    print(f"Completed query '{config.query}'. Total search results: {len(results)}")


if __name__ == "__main__":
    main()

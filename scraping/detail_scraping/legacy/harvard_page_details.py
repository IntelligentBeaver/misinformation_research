#!/usr/bin/env python3
"""
Scrape Harvard Health blog article pages into structured JSON.

Input list schema (expected):
    {
      "title": "...",
      "url": "...",
      "category": "Blog",
      "tags": ["Harvard Health Publishing", "Blog"]
    }

Output article schema:
    {
      "url": "...",
      "title": "...",
      "slug": "...",
      "published_date": "YYYY-MM-DD",
      "author": "...",
      "medically_reviewed_by": "...",
      "tags": ["Harvard Health Publishing", "Blog", "<article category>"],
      "meta_description": "...",
      "scrape_timestamp_utc": "...",
      "sections": [
        {
          "heading": "...",
          "content_blocks": [
            {
              "type": "paragraph",
              "text": "...",
              "associated_bullets": ["..."]
            }
          ]
        }
      ]
    }

Dependencies:
    pip install requests beautifulsoup4

Examples:
    # Normal run (all blog URLs)
    python 2_adapter_harvard_blogs_details.py

    # Test mode: process only one article
    python 2_adapter_harvard_blogs_details.py --test-mode

    # Test mode: process first 3 articles
    python 2_adapter_harvard_blogs_details.py --test-mode --test-count 3
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


# ----------------------------
# Configurable defaults
# ----------------------------
BASE_URL = "https://www.health.harvard.edu"

# DEFAULT_INPUT_JSON = "../../../storage/harvardhealth/harvard_blogs_list.json"
# DEFAULT_INPUT_JSON = "../../../storage/harvardhealth/harvard_health_topics.json"
DEFAULT_INPUT_JSON = "../../../storage/harvardhealth/harvard_medical_procedures_list.json"

# DEFAULT_OUTPUT_DIR = "../../../storage/harvardhealth/blogs"
# DEFAULT_OUTPUT_DIR = "../../../storage/harvardhealth/health_topics"
DEFAULT_OUTPUT_DIR = "../../../storage/harvardhealth/medical_procedures"


# DEFAULT_AGGREGATE_FILENAME = "harvard_blogs_articles.json"
# DEFAULT_AGGREGATE_FILENAME = "harvard_health_topics.json"
DEFAULT_AGGREGATE_FILENAME = "harvard_medical_procedures_articles.json"


DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_REQUEST_DELAY = 0.1
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; HarvardHealthBlogDetailsScraper/1.0)"

DEFAULT_TEST_MODE = False
DEFAULT_TEST_COUNT = 1
DEFAULT_MAX_ARTICLES: Optional[int] = None

DEFAULT_SKIP_EXISTING = True
DEFAULT_CONTINUE_ON_ERROR = True

DEFAULT_BASE_TAGS = ["Harvard Health Publishing", "Blog"]

CONTENT_CONTAINER_SELECTOR = "div.content-repository-content"


@dataclass
class ScrapeConfig:
    input_json: Path
    output_dir: Path
    aggregate_filename: str
    request_timeout: int
    request_delay: float
    user_agent: str
    test_mode: bool
    test_count: int
    max_articles: Optional[int]
    skip_existing: bool
    continue_on_error: bool


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_space(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def extract_text(node: Optional[Tag]) -> str:
    if not node:
        return ""
    return normalize_space(node.get_text(" ", strip=True))


def dedupe_keep_order(values: Iterable[Optional[str]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        if not value:
            continue
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def make_slug(url: str) -> str:
    path = urlparse(url).path
    slug = path.rstrip("/").split("/")[-1].strip().lower()
    slug = re.sub(r"[^a-z0-9\-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "article"


def normalize_date(raw: str) -> str:
    text = normalize_space(raw)
    if not text:
        return ""

    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)

    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return text


def load_list_items(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict) and row.get("url")]

    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return [row for row in data["items"] if isinstance(row, dict) and row.get("url")]

        for value in data.values():
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict) and row.get("url")]

    raise ValueError(f"Unsupported input JSON structure in: {path}")


def get_header_category(header: Optional[Tag]) -> str:
    if not header:
        return ""

    for a in header.select("a[href]"):
        if a.find_parent("address") or a.find_parent("ul"):
            continue
        text = extract_text(a)
        if text:
            return text
    return ""


def get_author(scope: Optional[Tag]) -> str:
    if not scope:
        return ""

    address = scope.select_one("address")
    if not address:
        return ""

    text = extract_text(address)
    text = re.sub(r"^By\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+,", ",", text)
    return text


def get_medically_reviewed_by(scope: Optional[Tag]) -> str:
    if not scope:
        return ""

    reviewed_li = scope.select_one("ul li")
    if not reviewed_li:
        return ""

    text = extract_text(reviewed_li)
    text = re.sub(r"^Reviewed\s+by\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+,", ",", text)
    return text


def get_published_date(scope: Optional[Tag]) -> str:
    if not scope:
        return ""

    time_tag = scope.select_one("time")
    if not time_tag:
        return ""

    datetime_attr = time_tag.get("datetime")
    raw = datetime_attr if isinstance(datetime_attr, str) else extract_text(time_tag)
    return normalize_date(raw)


def get_meta_description(soup: BeautifulSoup, header: Optional[Tag]) -> str:
    if header:
        h2 = header.find("h2")
        if h2:
            text = extract_text(h2)
            if text:
                return text

    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        content_attr = meta.get("content")
        if isinstance(content_attr, str):
            return normalize_space(content_attr)

    return ""


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
            if node.find("img"):
                continue
            if node.find_parent("li"):
                continue

            paragraph_text = extract_text(node)
            if not paragraph_text:
                continue

            current_section["content_blocks"].append(
                {
                    "type": "paragraph",
                    "text": paragraph_text,
                    "associated_bullets": None,
                }
            )
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

            current_section["content_blocks"].append(
                {
                    "type": "paragraph",
                    "text": None,
                    "associated_bullets": items,
                }
            )

    if current_section["heading"] is not None or current_section["content_blocks"]:
        sections.append(current_section)

    cleaned_sections: List[Dict] = []
    for sec in sections:
        content_blocks = sec.get("content_blocks") or []
        if not content_blocks and sec.get("heading") is None:
            continue
        cleaned_sections.append({"heading": sec.get("heading"), "content_blocks": content_blocks})

    return cleaned_sections


def extract_article(item: dict, html: str, final_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    article_scope = soup.select_one("article")
    header = (article_scope.select_one("header") if article_scope else None) or soup.select_one("article header") or soup.select_one("header")
    content_root = soup.select_one(CONTENT_CONTAINER_SELECTOR)

    title = extract_text((header.find("h1") if header else None) or soup.find("h1"))
    slug = item.get("slug") or make_slug(final_url)
    published_date = get_published_date(article_scope or header)
    author = get_author(article_scope or header)
    medically_reviewed_by = get_medically_reviewed_by(article_scope or header)
    meta_description = get_meta_description(soup, header)

    list_tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    category_from_list = item.get("category") if isinstance(item.get("category"), str) else ""
    category_from_header = get_header_category(header)

    tags = dedupe_keep_order(
        [
            *(list_tags or DEFAULT_BASE_TAGS),
            category_from_list,
            category_from_header,
        ]
    )

    sections = build_sections(content_root)

    if not title and item.get("title"):
        item_title = item.get("title")
        if isinstance(item_title, str):
            title = normalize_space(item_title)

    return {
        "url": final_url,
        "title": title,
        "slug": slug,
        "published_date": published_date,
        "author": author,
        "medically_reviewed_by": medically_reviewed_by,
        "tags": tags,
        "meta_description": meta_description,
        "scrape_timestamp_utc": utc_now_iso(),
        "sections": sections,
    }


def save_json(data, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_config_from_args() -> ScrapeConfig:
    parser = argparse.ArgumentParser(description="Harvard Health blog details scraper")
    parser.add_argument("--input-json", default=DEFAULT_INPUT_JSON, help="Path to blogs list JSON file.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory where per-article JSON files are written.")
    parser.add_argument(
        "--aggregate-file",
        default=DEFAULT_AGGREGATE_FILENAME,
        help="Aggregate output JSON filename (written in output-dir).",
    )
    parser.add_argument("--request-timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT, help="HTTP timeout in seconds.")
    parser.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY, help="Delay between requests in seconds.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent string.")
    parser.add_argument(
        "--test-mode",
        action="store_true",
        default=DEFAULT_TEST_MODE,
        help="Only process the first N records from input JSON.",
    )
    parser.add_argument(
        "--test-count",
        type=int,
        default=DEFAULT_TEST_COUNT,
        help="Number of records to process in test mode.",
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=DEFAULT_MAX_ARTICLES,
        help="Optional hard cap on number of records to process.",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Re-scrape and overwrite per-article JSON even if it already exists.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop immediately when any article fails.",
    )

    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    input_json = (script_dir / args.input_json).resolve()
    output_dir = (script_dir / args.output_dir).resolve()

    return ScrapeConfig(
        input_json=input_json,
        output_dir=output_dir,
        aggregate_filename=args.aggregate_file,
        request_timeout=args.request_timeout,
        request_delay=args.request_delay,
        user_agent=args.user_agent,
        test_mode=args.test_mode,
        test_count=max(1, args.test_count),
        max_articles=args.max_articles,
        skip_existing=(DEFAULT_SKIP_EXISTING and not args.no_skip_existing),
        continue_on_error=(DEFAULT_CONTINUE_ON_ERROR and not args.stop_on_error),
    )


def main() -> None:
    config = build_config_from_args()

    items = load_list_items(config.input_json)

    if config.test_mode:
        items = items[: config.test_count]
    if isinstance(config.max_articles, int) and config.max_articles > 0:
        items = items[: config.max_articles]

    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})

    results: List[dict] = []

    for idx, item in enumerate(items, start=1):
        url = item.get("url", "").strip()
        if not url:
            continue

        slug = item.get("slug") or make_slug(url)
        out_file = config.output_dir / f"{slug}.json"

        if config.skip_existing and out_file.exists():
            with out_file.open("r", encoding="utf-8") as f:
                try:
                    existing = json.load(f)
                    if isinstance(existing, dict):
                        results.append(existing)
                        print(f"[{idx}/{len(items)}] Skipped existing: {slug}")
                        continue
                except json.JSONDecodeError:
                    pass

        try:
            response = session.get(url, timeout=config.request_timeout)
            response.raise_for_status()

            article = extract_article(item=item, html=response.text, final_url=response.url)
            save_json(article, out_file)
            results.append(article)

            print(f"[{idx}/{len(items)}] Saved: {out_file.name}")
        except Exception as exc:
            error_obj = {
                "url": url,
                "slug": slug,
                "error": str(exc),
                "scrape_timestamp_utc": utc_now_iso(),
            }
            save_json(error_obj, config.output_dir / f"{slug}.error.json")

            if config.continue_on_error:
                print(f"[{idx}/{len(items)}] Error: {slug} -> {exc}")
                continue
            raise

        if config.request_delay > 0:
            time.sleep(config.request_delay)

    aggregate_path = config.output_dir / config.aggregate_filename
    save_json(results, aggregate_path)

    print(f"Processed {len(items)} input records")
    print(f"Wrote {len(results)} article records to: {aggregate_path}")


if __name__ == "__main__":
    main()

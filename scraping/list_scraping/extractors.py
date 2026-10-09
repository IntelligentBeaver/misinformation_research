#!/usr/bin/env python3
"""
Per-source HTML extraction for list (headline/index) scraping.

Each extractor receives the page HTML and the source config,
and returns a list of record dicts with keys: title, url, category, scraped_at, tags.

Extractors:
    extract_harvard_blogs           - Harvard Health blog list pages (card-based)
    extract_harvard_link_list       - Harvard decision guides, A-Z, procedures (link lists)
    extract_webmd_az                - WebMD A-Z health topic index pages
    extract_who_headlines           - WHO news room headlines
    extract_who_don                 - WHO Disease Outbreak News list pages
    extract_who_feature_stories     - WHO feature stories list pages

All extractors use dedupe_records() to remove duplicate URLs.

Usage:
    from scraping.list_scraping.extractors import get_extractor
    extractor = get_extractor("harvard_blogs")
    records = extractor(html, cfg)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scraping.common import (
    dedupe_records,
    normalize_space,
    parse_date_str,
    utc_now_iso,
)


# ----------------------------
# Link validation
# ----------------------------

def base_origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def is_valid_link(href: str, cfg: Dict[str, Any]) -> bool:
    if not href:
        return False
    absolute = urljoin(base_origin(cfg.get("target_url", "")), href)
    parsed = urlparse(absolute)

    url_filter = cfg.get("url_filter", {})
    netloc = url_filter.get("netloc")
    if netloc and parsed.netloc != netloc:
        return False
    path_prefix = url_filter.get("path_prefix")
    if path_prefix and not parsed.path.startswith(path_prefix):
        return False
    path_contains = url_filter.get("path_contains")
    if path_contains and path_contains not in parsed.path:
        return False
    return bool(parsed.path)


def absolute_url(href: str, cfg: Dict[str, Any]) -> str:
    return urljoin(base_origin(cfg.get("target_url", "")), href)


# ----------------------------
# Harvard Health Publishing
# ----------------------------

def extract_harvard_blogs(html: str, cfg: Dict[str, Any]) -> List[dict]:
    """Extract blog cards from Harvard Health blog list pages."""
    soup = BeautifulSoup(html, "html.parser")
    selectors = cfg.get("selectors", {})

    cards = soup.select(selectors.get("article_card", ""))
    if not cards:
        container = soup.select_one(selectors.get("article_list", ""))
        if not container:
            return []
        cards = container.find_all("div", recursive=False)

    scraped_at = utc_now_iso()
    record_defaults = cfg.get("record", {})
    records: List[dict] = []

    for card in cards:
        link = card.select_one(selectors.get("article_link", ""))
        if not link:
            continue

        href = (link.get("href") or "").strip()
        if not is_valid_link(href, cfg):
            continue

        title_node = card.select_one(selectors.get("title", "h2"))
        if title_node:
            title = title_node.get_text(" ", strip=True)
        else:
            title = link.get_text(" ", strip=True).replace("Read More about", "").strip()

        if not title:
            continue

        records.append(
            {
                "title": title,
                "url": absolute_url(href, cfg),
                "category": record_defaults.get("category", ""),
                "scraped_at": scraped_at,
                "tags": record_defaults.get("tags", []),
            }
        )

    return dedupe_records(records)


def extract_harvard_link_list(html: str, cfg: Dict[str, Any]) -> List[dict]:
    """Extract links from Harvard list pages (decision guides, A-to-Z, procedures).

    Handles a direct 'primary' anchor selector, a 'primary' container selector
    (anchors are looked up inside it), and a list of fallback selectors.
    """
    soup = BeautifulSoup(html, "html.parser")
    selectors = cfg.get("selectors", {})
    record_defaults = cfg.get("record", {})
    scraped_at = utc_now_iso()
    records: List[dict] = []

    def add_links(anchors) -> None:
        for a in anchors:
            href = (a.get("href") or "").strip()
            title = a.get_text(" ", strip=True)
            if not title or not is_valid_link(href, cfg):
                continue
            records.append(
                {
                    "title": title,
                    "url": absolute_url(href, cfg),
                    "category": record_defaults.get("category", ""),
                    "scraped_at": scraped_at,
                    "tags": record_defaults.get("tags", []),
                }
            )

    primary = selectors.get("primary")
    if primary:
        anchors: List[Any] = []
        for node in soup.select(primary):
            if getattr(node, "name", None) == "a":
                anchors.append(node)
            else:
                anchors.extend(node.select("li > a"))
                if not node.select("li > a"):
                    anchors.extend(node.find_all("a"))
        add_links(anchors)

    if not records:
        for selector in selectors.get("fallbacks", []):
            add_links(soup.select(selector))

    return dedupe_records(records)


# ----------------------------
# WebMD
# ----------------------------

def extract_webmd_az(html: str, cfg: Dict[str, Any]) -> List[dict]:
    """Extract topic links from a WebMD A-Z index page."""
    soup = BeautifulSoup(html, "html.parser")
    link_selector = cfg.get("selectors", {}).get(
        "link", "section.content-section ul.link-list li a"
    )
    tags = cfg.get("record", {}).get("tags", [])

    records: List[dict] = []
    for a in soup.select(link_selector):
        href = a.get("href")
        title = a.get_text(strip=True)
        if href and title:
            records.append({"url": href, "title": title, "tags": tags})

    return dedupe_records(records)


# ----------------------------
# WHO
# ----------------------------

def extract_who_headline_item(a_tag, base_url: str) -> Optional[dict]:
    """Extract one WHO headline item from an <a> tag."""
    href = a_tag.get("href")
    url = urljoin(base_url, href) if href else None

    title = a_tag.get("aria-label")
    if not title:
        p = a_tag.find("p", class_="heading")
        if p:
            title = p.get_text(strip=True)

    date_obj = None
    date_tag = a_tag.find("span", class_="timestamp")
    if date_tag:
        date_obj = parse_date_str(date_tag.get_text(strip=True))

    tags = [t.get_text(strip=True) for t in a_tag.select("div.sf-tags-list-item")] or None

    image = None
    img_div = a_tag.find("div", class_="background-image")
    if img_div:
        image = img_div.get("data-image")
        if not image:
            style = img_div.get("style") or ""
            match = re.search(r'url\((?:&quot;|")?(.*?)(?:&quot;|")?\)', style)
            if match:
                image = match.group(1)
        if image:
            image = urljoin(base_url, image)

    return {
        "url": url,
        "title": title,
        "date": date_obj.isoformat() if date_obj else None,
        "tags": tags,
        "image": image,
    }


def extract_who_headlines(html: str, cfg: Dict[str, Any]) -> List[dict]:
    """Extract headline items from a WHO headlines page."""
    soup = BeautifulSoup(html, "html.parser")
    selectors = cfg.get("selectors", {})
    container = soup.select_one(selectors.get("container", ""))
    if not container:
        return []

    base_url = base_origin(cfg.get("target_url", ""))
    anchor_selector = selectors.get("anchor", "a")

    records: List[dict] = []
    for a in container.select(anchor_selector):
        try:
            item = extract_who_headline_item(a, base_url)
        except Exception:
            continue
        if item and item.get("url"):
            records.append(item)

    return dedupe_records(records)


def extract_who_don(html: str, cfg: Dict[str, Any]) -> List[dict]:
    """Extract Disease Outbreak News items from a WHO DON page."""
    soup = BeautifulSoup(html, "html.parser")
    selectors = cfg.get("selectors", {})
    category = cfg.get("record", {}).get("category", "Disease Outbreak News")
    base_url = base_origin(cfg.get("target_url", ""))

    item_class = selectors.get("item_class", "sf-list-vertical__item")
    date_class = selectors.get("date_class", "sf-list-vertical__date")
    title_class = selectors.get("title_class", "sf-list-vertical__title")
    full_title_class = selectors.get("full_title_class", "full-title")
    trimmed_title_class = selectors.get("trimmed_title_class", "trimmed")

    items = soup.find_all("a", class_=item_class)
    records: List[dict] = []

    for item in items:
        date_div = item.find("div", class_=date_class)
        if not date_div or "Disease Outbreak News" not in date_div.get_text():
            continue

        url = item.get("href")
        if not url:
            continue
        url = urljoin(base_url, url)

        title = None
        date_text = None
        title_h4 = item.find("h4", class_=title_class)
        if title_h4:
            full_title_span = title_h4.find("span", class_=full_title_class)
            if full_title_span:
                title = full_title_span.get_text(strip=True)
            else:
                trimmed_span = title_h4.find("span", class_=trimmed_title_class)
                if trimmed_span:
                    title = trimmed_span.get_text(strip=True)

            for span in title_h4.find_all("span", recursive=False):
                text = span.get_text(strip=True)
                if "|" in text and any(c.isdigit() for c in text):
                    date_text = text.replace("|", "").strip()
                    break

        if title and url:
            records.append(
                {
                    "title": title,
                    "url": url,
                    "date": parse_date_str(date_text) if date_text else None,
                    "category": category,
                    "scraped_at": datetime.now().isoformat(),
                }
            )

    return dedupe_records(records)


def extract_who_feature_stories(html: str, cfg: Dict[str, Any]) -> List[dict]:
    """Extract feature story items from a WHO feature-stories page."""
    soup = BeautifulSoup(html, "html.parser")
    selectors = cfg.get("selectors", {})
    category = cfg.get("record", {}).get("category", "Feature Story")
    base_url = base_origin(cfg.get("target_url", ""))

    item_class = selectors.get("item_class", "list-view--item")
    link_class = selectors.get("link_class", "link-container")
    title_class = selectors.get("title_class", "heading")
    date_class = selectors.get("date_class", "date")
    timestamp_class = selectors.get("timestamp_class", "timestamp")

    items = soup.find_all("div", class_=item_class)
    records: List[dict] = []

    for item in items:
        link = item.find("a", class_=link_class)
        if not link:
            continue
        url = link.get("href")
        if not url:
            continue
        url = urljoin(base_url, url)

        title = None
        title_p = item.find("p", class_=title_class)
        if title_p:
            title = title_p.get_text(strip=True)

        date_text = None
        date_div = item.find("div", class_=date_class)
        if date_div:
            timestamp_span = date_div.find("span", class_=timestamp_class)
            if timestamp_span:
                date_text = timestamp_span.get_text(strip=True)

        if title and url:
            records.append(
                {
                    "title": title,
                    "url": url,
                    "date": parse_date_str(date_text) if date_text else None,
                    "category": category,
                    "scraped_at": datetime.now().isoformat(),
                }
            )

    return dedupe_records(records)


# ----------------------------
# Extractor registry
# ----------------------------

EXTRACTORS: Dict[str, Any] = {
    "harvard_blogs": extract_harvard_blogs,
    "harvard_decision_guides": extract_harvard_link_list,
    "harvard_healthtopics": extract_harvard_link_list,
    "harvard_medicalprocedures": extract_harvard_link_list,
    "webmd_healthtopics": extract_webmd_az,
    "who_news_headlines": extract_who_headlines,
    "who_disease_outbreak_headlines": extract_who_don,
    "who_feature_stories_headlines": extract_who_feature_stories,
}


def get_extractor(source_name: str):
    if source_name not in EXTRACTORS:
        raise KeyError(f"No extractor for source '{source_name}'")
    return EXTRACTORS[source_name]

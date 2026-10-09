#!/usr/bin/env python3
"""
Scrape WHO headlines pages (e.g. https://www.who.int/news-room/headlines, /headlines/3, ...)
Extracts: url, title, timestamp (ISO 8601), tag(s), image
Filters out articles older than --max-age-days (default 3652)
Groups results by year and saves JSON files under <output_dir>/<year>.json

Requirements:
    pip install requests beautifulsoup4
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.who.int"
HEADLINES_BASE = f"{BASE_URL}/news-room/headlines"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; WHO-Headlines-Scraper/1.0)"}

MAX_AGE_DAYS=7305
SEARCH_DELAY=0.1
OUTPUT_DIR="../../../storage/who/headlines"


def parse_date(date_str: str):
    """
    Parse date strings like '13 January 2026' or '9 January 2026' into a datetime.date.
    Returns a datetime.date or None if parsing fails.
    """
    if not date_str:
        return None
    date_str = date_str.strip()
    # remove odd whitespace characters
    date_str = re.sub(r"\s+", " ", date_str)
    # try common formats
    fmts = ["%d %B %Y", "%d %b %Y", "%d %B, %Y", "%d %b, %Y"]
    for f in fmts:
        try:
            return datetime.strptime(date_str, f).date()
        except Exception:
            pass
    # last resort: try splitting and reformatting if day is single-digit with non-ascii
    try:
        parts = date_str.split()
        if len(parts) >= 3:
            day = parts[0].zfill(2)
            month = parts[1]
            year = parts[2]
            for f in ["%d %B %Y", "%d %b %Y"]:
                try:
                    return datetime.strptime(f"{day} {month} {year}", f).date()
                except Exception:
                    pass
    except Exception:
        pass
    return None


def extract_item_from_anchor(a_tag, base_url=BASE_URL):
    """
    Given an <a> tag element for a WHO headline item, return a dict with fields:
      url, title, date (ISO), tags (list), image (absolute or None)
    """
    href = a_tag.get("href")
    url = urljoin(base_url, href) if href else None

    title = a_tag.get("aria-label")
    if not title:
        p = a_tag.find("p", class_="heading")
        if p:
            title = p.get_text(strip=True)

    # date
    date_tag = a_tag.find("span", class_="timestamp")
    date_obj = None
    if date_tag:
        date_obj = parse_date(date_tag.get_text(strip=True))

    # tags (there may be multiple sf-tags-list-item)
    tags = [t.get_text(strip=True) for t in a_tag.select("div.sf-tags-list-item")] or None

    # image: data-image or style url
    img = None
    img_div = a_tag.find("div", class_="background-image")
    if img_div:
        img = img_div.get("data-image")
        if not img:
            style = img_div.get("style") or ""
            m = re.search(r'url\((?:&quot;|")?(.*?)(?:&quot;|")?\)', style)
            if m:
                img = m.group(1)
        if img:
            img = urljoin(base_url, img)

    return {
        "url": url,
        "title": title,
        # store ISO date if we parsed it; otherwise None
        "date": date_obj.isoformat() if date_obj else None,
        "tags": tags,
        "image": img,
    }


def extract_links_from_page(page_url):
    """
    Fetch the page and return list of item dicts (using extract_item_from_anchor).
    """
    resp = requests.get(page_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    container = soup.select_one("div.list-view.vertical-list.vertical-list--image")
    if not container:
        return []

    items = []
    # find anchors that point to /news/item/
    anchors = container.select("a[href^='/news/item/']")
    for a in anchors:
        try:
            item = extract_item_from_anchor(a)
            # ensure it has a url or a title; skip otherwise
            if item.get("url"):
                items.append(item)
        except Exception as e:
            print(f"Warning: failed to parse an item: {e}", file=sys.stderr)
    return items


def scrape_pages(start_page=1, end_page=None, delay_seconds=0, max_age_days=365, output_dir="who/headlines"):
    """
    Scrape headlines pages beginning with start_page until:
      - a page returns no items OR
      - page > end_page (if end_page provided)

    Filter: Only keep items whose date is within max_age_days from today (if date is parsed).
            If an item has no parsable date, it will be excluded by default (safer).
    """
    os.makedirs(output_dir, exist_ok=True)
    cutoff_date = datetime.utcnow().date() - timedelta(days=max_age_days)
    all_items = []
    seen_urls = set()
    page = start_page

    while True:
        if page == 1:
            url = HEADLINES_BASE
        else:
            url = f"{HEADLINES_BASE}/{page}"

        print(f"Scraping page {page}: {url}")
        try:
            items = extract_links_from_page(url)
        except requests.RequestException as e:
            print(f"Request error for {url}: {e}. Stopping.", file=sys.stderr)
            break

        if not items:
            print("No items found on page (or container missing). Stopping.")
            break

        # filter & deduplicate
        new_count = 0
        for it in items:
            url = it.get("url")
            if not url or url in seen_urls:
                continue
            # parse ISO date back to date object for compare
            date_iso = it.get("date")
            if date_iso:
                try:
                    d = datetime.fromisoformat(date_iso).date()
                except Exception:
                    d = None
            else:
                d = None

            # Filter logic: include only if date exists and d >= cutoff_date
            if d is None:
                # skip items without parseable date (you can change this if you prefer to keep them)
                continue
            if d < cutoff_date:
                # too old
                continue

            seen_urls.add(url)
            all_items.append(it)
            new_count += 1

        print(f"  found {len(items)} items on page, kept {new_count} after filtering")

        # stop conditions
        page += 1
        if end_page and page > end_page:
            print("Reached end_page limit. Stopping.")
            break

        if delay_seconds:
            import time
            time.sleep(delay_seconds)

    # group by year and save JSON files
    grouped = {}
    for it in all_items:
        date_iso = it.get("date")
        if date_iso:
            year = date_iso.split("-")[0]
        else:
            year = "unknown"
        grouped.setdefault(year, []).append(it)

    for year, items in grouped.items():
        filename = os.path.join(output_dir, f"{year}.json")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"Wrote {len(items)} items to {filename}")

    return grouped


def main():
    parser = argparse.ArgumentParser(description="WHO headlines scraper with date filtering and yearly JSON output.")
    parser.add_argument("--start-page", type=int, default=1, help="Start page number (default 1).")
    parser.add_argument("--end-page", type=int, default=None, help="End page number (inclusive). If omitted, continues until no more pages.")
    # Configure this
    parser.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS, help="Maximum age of article in days to keep (default 3652- 10 years).")
    # Configure this
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between page requests in seconds (politeness).")
    parser.add_argument("--output-dir", type=str, default=OUTPUT_DIR, help="Directory to save year JSON files.")
    args = parser.parse_args()

    grouped = scrape_pages(
        start_page=args.start_page,
        end_page=args.end_page,
        delay_seconds=args.delay,
        max_age_days=args.max_age_days,
        output_dir=args.output_dir,
    )

    total = sum(len(v) for v in grouped.values())
    print(f"\nDone. Extracted {total} articles across {len(grouped)} year(s).")


if __name__ == "__main__":
    main()

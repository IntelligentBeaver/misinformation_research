"""
WebMD Health Topics Scraper

Creates a JSON list of health topics from WebMD A-Z index pages.

Outputs JSON objects like:
[
  {
    "url": "https://www.webmd.com/....",
    "title": "Topic Title",
    "date": "YYYY-MM-DD" or "",
    "tags": ["Health Topics", "WebMD"],
    "image": "https://..." or ""
  },
  ...
]

Usage:
  python webmd_health_topics_scraper.py --letters a b c --output topics.json
  python webmd_health_topics_scraper.py --letters all --fetch-details --delay 1.5

Notes:
- Respects rate limiting via `delay` parameter (default 1.0s).
- By default it scrapes only the A-Z index pages and collects title + URL.
- If --fetch-details is given the scraper will also request each topic page
  and attempt to extract an `og:image` and a published date (if available).
- Make sure your usage follows WebMD's Terms of Service and robots.txt.

Dependencies:
  pip install requests beautifulsoup4

"""

import argparse
import json
import logging
import re
import time
from datetime import datetime
from typing import List, Dict

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_INDEX_URL = "https://www.webmd.com/a-to-z-guides/health-topics"
HEADERS = {
    "User-Agent": "webmd-scraper/1.0 (+https://your-research.example)"
}
DEFAULT_TAGS = ["Health Topics", "WebMD"]


def requests_session_with_retries(total_retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    s = requests.Session()
    retries = Retry(total=total_retries, backoff_factor=backoff_factor,
                    status_forcelist=[429, 500, 502, 503, 504], allowed_methods=frozenset(["GET"]))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update(HEADERS)
    return s


def build_index_url(letter: str) -> str:
    letter = letter.lower()
    if letter == "a":
        return BASE_INDEX_URL
    return f"{BASE_INDEX_URL}?pg={letter}"


def parse_index_page(html: str) -> List[Dict[str, str]]:
    """Parse the index page HTML and return list of {url, title}."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # The user's example shows the list inside: section.content-section ul.link-list li a
    for a in soup.select("section.content-section ul.link-list li a"):
        href = a.get("href")
        title = a.get_text(strip=True)
        if href and title:
            results.append({"url": href, "title": title})
    return results


def extract_details_from_topic_page(html: str) -> Dict[str, str]:
    """Attempt to extract publish date and og:image from a topic page.
    Returns dict with keys: date (YYYY-MM-DD or empty), image (url or empty).
    """
    soup = BeautifulSoup(html, "html.parser")
    date_str = ""
    image = ""

    # Try common meta tags first
    # article:published_time
    meta_pub = soup.find("meta", attrs={"property": "article:published_time"})
    if meta_pub and meta_pub.get("content"):
        date_str = meta_pub["content"]
    # fallback to meta[name="pubdate"]
    if not date_str:
        meta_pub2 = soup.find("meta", attrs={"name": "pubdate"})
        if meta_pub2 and meta_pub2.get("content"):
            date_str = meta_pub2["content"]
    # fallback to <time datetime=>
    if not date_str:
        time_tag = soup.find("time")
        if time_tag and (time_tag.get("datetime") or time_tag.get_text(strip=True)):
            date_str = time_tag.get("datetime") or time_tag.get_text(strip=True)

    # Try to normalize date to YYYY-MM-DD if possible
    date_out = ""
    if date_str:
        # Some pages use ISO 8601; try to extract YYYY-MM-DD with regex first
        m = re.search(r"(\d{4}-\d{2}-\d{2})", date_str)
        if m:
            date_out = m.group(1)
        else:
            # Try common formats like 'January 2, 2016'
            try:
                parsed = datetime.strptime(date_str.strip(), "%B %d, %Y")
                date_out = parsed.strftime("%Y-%m-%d")
            except Exception:
                # last resort: leave blank
                date_out = ""

    # og:image
    meta_img = soup.find("meta", attrs={"property": "og:image"})
    if meta_img and meta_img.get("content"):
        image = meta_img["content"]
    else:
        # look for large images commonly used on pages
        img_tag = soup.select_one(".article-image img, .hero img, img[src]")
        if img_tag and img_tag.get("src"):
            image = img_tag.get("src")

    return {"date": date_out, "image": image or ""}


def scrape_health_topics_for_letter(letter: str, session: requests.Session, delay: float = 1.0) -> List[Dict]:
    url = build_index_url(letter)
    logging.info(f"Fetching index page for letter '{letter}' -> {url}")
    r = session.get(url, timeout=15)
    r.raise_for_status()

    entries = parse_index_page(r.text)
    output = []

    for e in entries:
        item = {
            "url": e["url"],
            "title": e["title"],
            "tags": DEFAULT_TAGS,
        }
        # if fetch_details:
        #     try:
        #         logging.debug(f"Fetching details for {e['url']}")
        #         rr = session.get(e["url"], timeout=15)
        #         rr.raise_for_status()
        #         details = extract_details_from_topic_page(rr.text)
        #         item["date"] = details.get("date", "")
        #         item["image"] = details.get("image", "")
        #     except Exception as ex:
        #         logging.warning(f"Failed to fetch details for {e['url']}: {ex}")
        #     time.sleep(delay)
        output.append(item)

    return output


def scrape_letters(letters: List[str], fetch_details: bool = False, delay: float = 1.0) -> List[Dict]:
    session = requests_session_with_retries()
    all_items = []
    for letter in letters:
        try:
            items = scrape_health_topics_for_letter(letter, session, delay=delay)
            all_items.extend(items)
        except Exception as e:
            logging.error(f"Error scraping letter '{letter}': {e}")
        time.sleep(delay)
    return all_items


def letters_from_arg(arg_letters: List[str]) -> List[str]:
    # support 'all' and single letters
    if not arg_letters:
        return ["a"]
    if len(arg_letters) == 1 and arg_letters[0].lower() == "all":
        return [chr(c) for c in range(ord("a"), ord("z") + 1)]
    # flatten comma-separated
    out = []
    for part in arg_letters:
        part = part.strip()
        if "," in part:
            out.extend([p.strip() for p in part.split(",") if p.strip()])
        else:
            out.append(part)
    return out


def save_json(output: List[Dict], outpath: str):
    with open(outpath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Scrape WebMD health topics (A-Z index) and produce JSON output")
    parser.add_argument("--letters", "-l", nargs="*", default=["all"], help="letters to scrape (e.g. a b c) or 'all'")
    # parser.add_argument("--fetch-details", "-d", action="store_true", help="Also fetch each topic page to extract date and image (slower)")
    parser.add_argument("--output", "-o", default="webmd_health_topics.json", help="Output JSON filename")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between requests (rate limit)")
    parser.add_argument("--log", default="info", help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log.upper(), logging.INFO), format="%(levelname)s: %(message)s")

    letters = letters_from_arg(args.letters)
    logging.info(f"Scraping letters: {letters}")
    items = scrape_letters(letters, delay=args.delay)
    logging.info(f"Scraped {len(items)} items")
    save_json(items, args.output)
    logging.info(f"Saved output to {args.output}")


if __name__ == "__main__":
    main()
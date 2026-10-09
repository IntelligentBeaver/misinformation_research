#!/usr/bin/env python3
"""
adapter_webmd_healthtopics.py

Read an input JSON of topic URLs (the index scraper output), fetch each
WebMD topic page, extract structured details, and write one JSON file per topic.

Usage:
  python webmd_topic_details_fetcher.py --input webmd_health_topics.json --output-dir webmd_topics --aggregate all_topics.json --delay 1.0

Dependencies:
  pip install requests beautifulsoup4
"""
import argparse
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TAGS = ["Health Topics", "WebMD"]
HEADERS = {"User-Agent": "webmd-topic-scraper/1.0 (+https://verifact.scrape)"}

OUTPUT_DIR="../../storage/webmd/healthtopics"
INPUT_DIR="../../storage/webmd/webmd_health_topics.json"


def requests_session_with_retries(total_retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update(HEADERS)
    return s


def _safe_text(el) -> str:
    return el.get_text(separator=" ", strip=True) if el else ""


def _make_slug_from_url(url: str) -> str:
    path = urlparse(url).path
    slug = path.rstrip("/").split("/")[-1]
    slug = re.sub(r"[^a-zA-Z0-9\-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-").lower()
    return slug or "topic"


def _normalize_date_str(date_str: str) -> str:
    if not date_str:
        return ""
    date_str = date_str.strip()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", date_str)
    if m:
        return m.group(1)
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", date_str)
    if m:
        return m.group(1)
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return date_str


def extract_topic_details(html: str, url: Optional[str] = None) -> Dict:
    """Parse a WebMD health-topic HTML page and return structured data."""
    soup = BeautifulSoup(html, "html.parser")

    title = _safe_text(soup.find("h1")) or _safe_text(soup.find("title"))
    slug = _make_slug_from_url(url) if url else (title.lower().replace(" ", "-") if title else "")
    first_letter = (slug[0].upper() if slug else (title[0].upper() if title else ""))

    # canonical and meta description
    canonical = None
    c_el = soup.find("link", rel="canonical")
    if c_el and c_el.get("href"):
        canonical = c_el.get("href")

    meta_desc = None
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        meta_desc = md.get("content")

    # --- Improved author / reviewer / published date extraction ---
    author = ""
    medically_reviewed_by = ""
    published_date = ""  # final normalized date string

    # Try to get authors from the reviewer-info .authors span (preferred)
    authors_span = soup.select_one(".reviewer-info .authors")
    if authors_span:
        # Collect <a class="person"> names if present
        names = [a.get_text(strip=True) for a in authors_span.select("a.person") if a.get_text(strip=True)]
        if not names:
            # fallback: parse text node like "Written by Alyson Powell Key, Shawna Seed"
            txt = _safe_text(authors_span)
            m = re.search(r"Written by\s*[:\-]?\s*(.+)", txt, re.I)
            if m:
                # split on commas or ' and '
                parts = re.split(r",|\band\b", m.group(1))
                names = [p.strip() for p in parts if p.strip()]
        author = ", ".join(names).strip()

    # Try to get medically reviewed by and the review date from reviewer-info .reviewer-txt
    rev_span = soup.select_one(".reviewer-info .reviewer-txt")
    if rev_span:
        # reviewer name (link or plain text)
        rev_person = rev_span.select_one("a.person")
        if rev_person and _safe_text(rev_person):
            medically_reviewed_by = _safe_text(rev_person)
        else:
            # fallback: regex on the reviewer text
            rev_txt = _safe_text(rev_span)
            mrev = re.search(r"Medically Reviewed by\s*[:\-]?\s*([^,|on]+)", rev_txt, re.I)
            if mrev:
                medically_reviewed_by = mrev.group(1).strip()

        # review date is often in .revDate (e.g. ' on November 25, 2025')
        rev_date_el = rev_span.select_one(".revDate")
        if rev_date_el:
            rev_date_text = _safe_text(rev_date_el)
            # strip leading 'on' or stray punctuation
            rev_date_text = re.sub(r"^[Oo]n[\s:,-]*", "", rev_date_text).strip()
            published_date = _normalize_date_str(rev_date_text)
        else:
            # fallback: try to find 'on <date>' inside the reviewer text
            mdate = re.search(r"\bon\s+([A-Za-z0-9, \-]+)", _safe_text(rev_span), re.I)
            if mdate:
                published_date = _normalize_date_str(mdate.group(1).strip())

    # If we still don't have published_date, try other meta/time tags (existing logic as fallback)
    if not published_date:
        # meta property article:published_time
        meta_pub = soup.find("meta", attrs={"property": "article:published_time"})
        if meta_pub and meta_pub.get("content"):
            published_date = _normalize_date_str(meta_pub.get("content"))
        else:
            meta_pub2 = soup.find("meta", attrs={"name": "pubdate"})
            if meta_pub2 and meta_pub2.get("content"):
                published_date = _normalize_date_str(meta_pub2.get("content"))
            else:
                time_tag = soup.find("time")
                if time_tag:
                    published_date = _normalize_date_str(time_tag.get("datetime") or _safe_text(time_tag))
                else:
                    # last resort: look for 'Published' or 'Updated' near top of page text
                    txt_all = soup.get_text(separator="||", strip=True)
                    m2 = re.search(r"(Published|Updated)\s*[:\-]?\s*([A-Za-z0-9, \-]+)", txt_all)
                    if m2:
                        published_date = _normalize_date_str(m2.group(2))

    # Ensure medically_reviewed_by is trimmed and author is normalized (no stray separators)
    author = re.sub(r"\s+[,;]\s+", ", ", author).strip()
    medically_reviewed_by = medically_reviewed_by.strip()

    # Read time
    read_time = ""
    rt = soup.find(string=re.compile(r"\bmin read\b", re.I))
    if rt:
        read_time = rt.strip()
    else:
        rt_el = soup.select_one(".reading-time, .read-time, .article-read-time")
        if rt_el:
            read_time = _safe_text(rt_el)

    # Images (og:image + article images)
    images = []
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        images.append(og.get("content"))
    for img in soup.select(".article-body img, .main-article img, .article-image img, img"):
        src = img.get("data-src") or img.get("src") or img.get("data-lazy-src")
        if src and src.startswith("http") and src not in images:
            images.append(src)

    # PDFs: any links to .pdf
    pdfs = []
    for a in soup.select("a[href]"):
        href = a.get("href")
        if href and href.lower().endswith(".pdf"):
            pdfs.append(href)

    # Related links
    related_links = []
    for sel in [".related-links a", ".related a", ".module-related a", ".more-like-this a"]:
        for a in soup.select(sel):
            href = a.get("href")
            text = _safe_text(a)
            if href and href.startswith("http"):
                related_links.append({"url": href, "text": text})

    # Hidden sources section
    sources = []
    src_div = soup.select_one("div.sources-section, .sources-section")
    if src_div:
        for p in src_div.find_all(["p"]):
            txt = _safe_text(p)
            if not txt:
                continue
            if re.match(r"SOURCES\s*[:]?$", txt, re.I):
                continue
            sources.append(txt)
    else:
        msrc = re.search(r"Sources?\s*[:\-]\s*(.+)$", soup.get_text(separator="\n"), re.I | re.M)
        if msrc:
            sources.append(msrc.group(1).strip())

    # --- Robust sections extraction: handle .article-page, headerless <section>, instream-mods, pagebreak, <strong> pseudo-headings ---
    sections = []
    # Container already selected above
    container = soup.select_one('.article-body, .article-content, .main-article, #article') or soup.body

    def _paragraph_introduces_list(paragraph_text: str) -> bool:
        if not paragraph_text:
            return False
        pt = paragraph_text.strip()
        if pt.endswith(':') or pt.endswith('such as:'):
            return True
        cues = [
            r'\bsuch as\b', r'\bincluding\b', r'\bincludes\b', r'\binclude\b',
            r'\bgiven below\b', r'\bfor example\b', r'\bfor instance\b',
            r'\bthe following\b', r'\bexamples?\b', r'\blike\b'
        ]
        for cue in cues:
            if re.search(cue, pt, re.I):
                return True
        return False

    # Iterate through pages if paginated, otherwise process container as single page
    pages = container.select('.article-page') if container.select('.article-page') else [container]

    for page in pages:
        # find all section blocks inside this page, preserving order
        for sec in page.find_all('section', recursive=False) + page.find_all('section', recursive=True):
            # skip if this is an empty wrapper we already processed (avoid duplicates)
            if not getattr(sec, 'name', None):
                continue

            # skip obvious ad modules
            sec_cls = ' '.join(sec.get('class') or [])
            if 'ad' in sec_cls.lower() or 'advert' in sec_cls.lower():
                continue

            # Determine heading: prefer h2/h3 inside the section
            heading_el = sec.find(['h2', 'h3'])
            heading_text = _safe_text(heading_el) if heading_el else None

            # If no heading and the first paragraph starts with <strong>, treat that strong text as a pseudo-heading
            if not heading_text:
                first_p = sec.find('p')
                if first_p:
                    strong = first_p.find('strong') or first_p.find('b')
                    if strong and _safe_text(strong):
                        # use strong text as heading, and remove it from the first paragraph content
                        heading_text = _safe_text(strong)
                        # remove strong element content from the paragraph text for the paragraph body
                        # create a copy of first_p text without the strong portion
                        # We will handle paragraph extraction below, so mark this strong so it won't duplicate
                        # Remove the strong tag so downstream p extraction sees the remainder only
                        try:
                            strong.extract()
                        except Exception:
                            pass

            # Collect content blocks in order
            content_blocks = []
            section_bullets_combined = []

            # iterate direct children of section in document order
            for child in sec.children:
                if getattr(child, 'name', None) is None:
                    continue
                tag = child.name.lower()

                # skip pagebreak tags or similar
                if tag == 'pagebreak':
                    continue

                # Paragraphs
                if tag == 'p':
                    txt = _safe_text(child).replace('\xa0', ' ').strip()
                    if txt:
                        content_blocks.append({'type': 'paragraph', 'text': txt, 'associated_bullets': None})

                # Lists
                elif tag in ['ul', 'ol']:
                    items = []
                    for li in child.find_all('li', recursive=False):
                        t = _safe_text(li).replace('\xa0', ' ').strip()
                        if t:
                            items.append(t)
                    if items:
                        # attach to previous paragraph if it appears to introduce this list
                        if content_blocks and content_blocks[-1]['type'] == 'paragraph' and _paragraph_introduces_list(
                                content_blocks[-1]['text']):
                            content_blocks[-1]['associated_bullets'] = items
                        else:
                            content_blocks.append({'type': 'bullets', 'items': items})
                        section_bullets_combined.extend(items)

                # Common wrappers that may contain paragraphs and lists (figure/aside/div)
                elif tag in ['div', 'aside', 'figure', 'article', 'section']:
                    cls = ' '.join(child.get('class') or [])
                    if 'ad' in cls.lower() or 'instream-related-mod' in cls.lower():
                        # if it's a related/instream module, try to capture a link label as a related link entry,
                        # but do not treat as main section content.
                        for a in child.select('a[href]'):
                            href = a.get('href')
                            txt = _safe_text(a)
                            if href and href.startswith('http'):
                                related_links.append({'url': href, 'text': txt})
                        continue

                    # extract paragraphs inside wrapper in order
                    for p in child.find_all('p', recursive=True):
                        txt = _safe_text(p).replace('\xa0', ' ').strip()
                        if txt:
                            content_blocks.append({'type': 'paragraph', 'text': txt, 'associated_bullets': None})

                    # extract lists inside wrapper
                    for ul in child.find_all(['ul', 'ol'], recursive=True):
                        items = []
                        for li in ul.find_all('li', recursive=False):
                            t = _safe_text(li).replace('\xa0', ' ').strip()
                            if t:
                                items.append(t)
                        if items:
                            # attach to last paragraph if it introduces a list
                            if content_blocks and content_blocks[-1][
                                'type'] == 'paragraph' and _paragraph_introduces_list(content_blocks[-1]['text']):
                                content_blocks[-1]['associated_bullets'] = items
                            else:
                                content_blocks.append({'type': 'bullets', 'items': items})
                            section_bullets_combined.extend(items)

                else:
                    # catch-all: try to pick up nested p or lists if present
                    for p in child.select('p'):
                        txt = _safe_text(p).replace('\xa0', ' ').strip()
                        if txt:
                            content_blocks.append({'type': 'paragraph', 'text': txt, 'associated_bullets': None})
                    for ul in child.select('ul, ol'):
                        items = []
                        for li in ul.find_all('li'):
                            t = _safe_text(li).replace('\xa0', ' ').strip()
                            if t:
                                items.append(t)
                        if items:
                            if content_blocks and content_blocks[-1][
                                'type'] == 'paragraph' and _paragraph_introduces_list(content_blocks[-1]['text']):
                                content_blocks[-1]['associated_bullets'] = items
                            else:
                                content_blocks.append({'type': 'bullets', 'items': items})
                            section_bullets_combined.extend(items)

            # Build backward-compatible fields
            paragraph_texts = [b['text'] for b in content_blocks if b['type'] == 'paragraph']
            bullets_list = section_bullets_combined if section_bullets_combined else None

            # If heading is still None but there is at least one paragraph, consider first paragraph as overview (optional)
            if not heading_text and paragraph_texts:
                # leave heading as None (or set to 'Overview' if you prefer)
                heading_out = None
            else:
                heading_out = heading_text

            sections.append({
                'heading': heading_out,
                'content': paragraph_texts if paragraph_texts else None,
                'bullets': bullets_list,
                'content_blocks': content_blocks
            })

    # Fallback if no sections at all
    if not sections:
        intro_ps = container.select('p')[:8]
        content_blocks = []
        for p in intro_ps:
            txt = _safe_text(p).replace('\xa0', ' ').strip()
            if txt:
                content_blocks.append({'type': 'paragraph', 'text': txt, 'associated_bullets': None})
        paragraph_texts = [b['text'] for b in content_blocks]
        if paragraph_texts:
            sections.append({
                'heading': 'Overview',
                'content': paragraph_texts,
                'bullets': None,
                'content_blocks': content_blocks
            })

    # Fallback values
    if not canonical and url:
        canonical = url
    if not meta_desc:
        first_p = container.find("p") if container else None
        meta_desc = _safe_text(first_p)

    result = {
        "url": url or "",
        "title": title or "",
        "slug": slug or "",
        "published_date": published_date or "",
        "first_letter": first_letter or "",
        "author": author or "",
        "medically_reviewed_by": medically_reviewed_by or "",
        "read_time": read_time or "",
        "sections": sections,
        "pdfs": list(dict.fromkeys(pdfs)),
        "images": images,
        "related_links": related_links,
        "sources": sources,
        "meta_description": meta_desc or "",
        "canonical_url": canonical or "",
        "tags": DEFAULT_TAGS,
        "scrape_timestamp_utc": datetime.utcnow().isoformat() + "Z",
    }

    return result


def load_input_json(path: str) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    if isinstance(data, list):
        return data
    for v in data.values():
        if isinstance(v, list):
            return v
    raise ValueError("Input JSON does not contain a list of items")


def save_json(obj: Dict, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def fetch_and_save_all(input_json: str, output_dir: str, aggregate_path: Optional[str] = None, delay: float = 1.0, log_level: str = "INFO") -> List[Dict]:
    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO), format="%(levelname)s: %(message)s")
    items = load_input_json(input_json)
    session = requests_session_with_retries()

    results = []
    total = len(items)
    logging.info(f"Found {total} input items to process")

    for idx, item in enumerate(items, start=1):
        url = item.get("url") if isinstance(item, dict) else item
        if not url:
            logging.warning(f"Skipping item {idx}: no URL found")
            continue
        logging.info(f"[{idx}/{total}] Fetching {url}")
        try:
            r = session.get(url, timeout=20)
            r.encoding = r.apparent_encoding or 'utf-8'

            r.raise_for_status()
            details = extract_topic_details(r.text, url=url)
            if isinstance(item, dict) and item.get("title") and not details.get("title"):
                details["title"] = item.get("title")
            slug = details.get("slug") or _make_slug_from_url(url) or f"topic-{idx}"
            filename = f"{slug}.json"
            outpath = os.path.join(output_dir, filename)
            save_json(details, outpath)
            results.append(details)
            logging.info(f"Saved {outpath}")
        except Exception as e:
            logging.error(f"Failed to fetch or parse {url}: {e}")
            err_obj = {"url": url, "error": str(e), "scrape_timestamp_utc": datetime.utcnow().isoformat() + "Z"}
            slug = _make_slug_from_url(url) or f"topic-{idx}"
            outpath = os.path.join(output_dir, f"{slug}.error.json")
            save_json(err_obj, outpath)
        time.sleep(delay)

    if aggregate_path:
        save_json(results, aggregate_path)
        logging.info(f"Wrote aggregate file to {aggregate_path}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Fetch WebMD topic pages and save detailed JSON files per topic")
    parser.add_argument("--input", "-i", default=INPUT_DIR, help="Input JSON file containing list of topic URLs")
    parser.add_argument("--output-dir", "-o", default=OUTPUT_DIR, help="Directory to write per-topic JSON files")
    parser.add_argument("--aggregate", "-a", default=None, help="Optional aggregated JSON file path to write all results")
    parser.add_argument("--delay", type=float, default=0.1, help="Seconds to wait between requests")
    parser.add_argument("--log", default="info", help="Logging level (debug/info/warning/error)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    # INPUT_FILE="webmd_sample.json"
    fetch_and_save_all(args.input, args.output_dir, aggregate_path=args.aggregate, delay=args.delay, log_level=args.log)


if __name__ == "__main__":
    main()
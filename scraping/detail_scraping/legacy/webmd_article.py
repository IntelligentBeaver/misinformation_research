#!/usr/bin/env python3
"""
Multithreaded WebMD topic details fetcher with year-wise output.

Usage example:
  python webmd_topic_details_fetcher_threaded_by_year.py \
      --input ../../storage/webmd/webmd_articles_list_cleaned.json \
      --output-dir ../../storage/webmd/healthtopics_mt \
      --aggregate all_topics_mt.json \
      --workers 8 --delay 0.2

Options of interest:
  --no-year-files      : do not write year-wise files
  --year-dir NAME      : directory name for year files inside output-dir (default: "by_year")
  --wrap-year-meta     : wrap each year JSON in an object {"year": "2025", "count": N, "items": [...]}

Dependencies:
  pip install requests beautifulsoup4
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import re
import time
import threading
import concurrent.futures
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Configuration / defaults ---
DEFAULT_TAGS = ["Health Topics", "WebMD"]
HEADERS = {"User-Agent": "webmd-topic-scraper/1.0 (+https://verifact.scrape)"}

OUTPUT_DIR = "../../storage/webmd/articles"
INPUT_FILE = "../../storage/webmd/webmd_articles_list_cleaned.json"


# --- Networking helpers ---
def requests_session_with_retries(total_retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.headers.update(HEADERS)
    return s


# --- Small helpers copied from your original script ---
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


# --- The main parsing function (kept mostly unchanged) ---
def extract_topic_details(html: str, url: Optional[str] = None) -> Dict:
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

    # author/reviewer/published
    author = ""
    medically_reviewed_by = ""
    published_date = ""

    authors_span = soup.select_one(".reviewer-info .authors")
    if authors_span:
        names = [a.get_text(strip=True) for a in authors_span.select("a.person") if a.get_text(strip=True)]
        if not names:
            txt = _safe_text(authors_span)
            m = re.search(r"Written by\s*[:\-]?\s*(.+)", txt, re.I)
            if m:
                parts = re.split(r",|\band\b", m.group(1))
                names = [p.strip() for p in parts if p.strip()]
        author = ", ".join(names).strip()

    rev_span = soup.select_one(".reviewer-info .reviewer-txt")
    if rev_span:
        rev_person = rev_span.select_one("a.person")
        if rev_person and _safe_text(rev_person):
            medically_reviewed_by = _safe_text(rev_person)
        else:
            rev_txt = _safe_text(rev_span)
            mrev = re.search(r"Medically Reviewed by\s*[:\-]?\s*([^,|on]+)", rev_txt, re.I)
            if mrev:
                medically_reviewed_by = mrev.group(1).strip()

        rev_date_el = rev_span.select_one(".revDate")
        if rev_date_el:
            rev_date_text = _safe_text(rev_date_el)
            rev_date_text = re.sub(r"^[Oo]n[\s:,-]*", "", rev_date_text).strip()
            published_date = _normalize_date_str(rev_date_text)
        else:
            mdate = re.search(r"\bon\s+([A-Za-z0-9, \-]+)", _safe_text(rev_span), re.I)
            if mdate:
                published_date = _normalize_date_str(mdate.group(1).strip())

    if not published_date:
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
                    txt_all = soup.get_text(separator="||", strip=True)
                    m2 = re.search(r"(Published|Updated)\s*[:\-]?\s*([A-Za-z0-9, \-]+)", txt_all)
                    if m2:
                        published_date = _normalize_date_str(m2.group(2))

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

    # Images
    images = []
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        images.append(og.get("content"))
    for img in soup.select(".article-body img, .main-article img, .article-image img, img"):
        src = img.get("data-src") or img.get("src") or img.get("data-lazy-src")
        if src and src.startswith("http") and src not in images:
            images.append(src)

    # PDFs
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

    # Sources
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

    # Sections extraction (robust)
    sections = []
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

    pages = container.select('.article-page') if container.select('.article-page') else [container]

    for page in pages:
        for sec in page.find_all('section', recursive=False) + page.find_all('section', recursive=True):
            if not getattr(sec, 'name', None):
                continue
            sec_cls = ' '.join(sec.get('class') or [])
            if 'ad' in sec_cls.lower() or 'advert' in sec_cls.lower():
                continue

            heading_el = sec.find(['h2', 'h3'])
            heading_text = _safe_text(heading_el) if heading_el else None

            if not heading_text:
                first_p = sec.find('p')
                if first_p:
                    strong = first_p.find('strong') or first_p.find('b')
                    if strong and _safe_text(strong):
                        heading_text = _safe_text(strong)
                        try:
                            strong.extract()
                        except Exception:
                            pass

            content_blocks = []
            section_bullets_combined = []

            for child in sec.children:
                if getattr(child, 'name', None) is None:
                    continue
                tag = child.name.lower()
                if tag == 'pagebreak':
                    continue

                if tag == 'p':
                    txt = _safe_text(child).replace('\xa0', ' ').strip()
                    if txt:
                        content_blocks.append({'type': 'paragraph', 'text': txt, 'associated_bullets': None})

                elif tag in ['ul', 'ol']:
                    items = []
                    for li in child.find_all('li', recursive=False):
                        t = _safe_text(li).replace('\xa0', ' ').strip()
                        if t:
                            items.append(t)
                    if items:
                        if content_blocks and content_blocks[-1]['type'] == 'paragraph' and _paragraph_introduces_list(
                                content_blocks[-1]['text']):
                            content_blocks[-1]['associated_bullets'] = items
                        else:
                            content_blocks.append({'type': 'bullets', 'items': items})
                        section_bullets_combined.extend(items)

                elif tag in ['div', 'aside', 'figure', 'article', 'section']:
                    cls = ' '.join(child.get('class') or [])
                    if 'ad' in cls.lower() or 'instream-related-mod' in cls.lower():
                        for a in child.select('a[href]'):
                            href = a.get('href')
                            txt = _safe_text(a)
                            if href and href.startswith('http'):
                                related_links.append({'url': href, 'text': txt})
                        continue

                    for p in child.find_all('p', recursive=True):
                        txt = _safe_text(p).replace('\xa0', ' ').strip()
                        if txt:
                            content_blocks.append({'type': 'paragraph', 'text': txt, 'associated_bullets': None})

                    for ul in child.find_all(['ul', 'ol'], recursive=True):
                        items = []
                        for li in ul.find_all('li', recursive=False):
                            t = _safe_text(li).replace('\xa0', ' ').strip()
                            if t:
                                items.append(t)
                        if items:
                            if content_blocks and content_blocks[-1]['type'] == 'paragraph' and _paragraph_introduces_list(content_blocks[-1]['text']):
                                content_blocks[-1]['associated_bullets'] = items
                            else:
                                content_blocks.append({'type': 'bullets', 'items': items})
                            section_bullets_combined.extend(items)
                else:
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
                            if content_blocks and content_blocks[-1]['type'] == 'paragraph' and _paragraph_introduces_list(content_blocks[-1]['text']):
                                content_blocks[-1]['associated_bullets'] = items
                            else:
                                content_blocks.append({'type': 'bullets', 'items': items})
                            section_bullets_combined.extend(items)

            paragraph_texts = [b['text'] for b in content_blocks if b['type'] == 'paragraph']
            bullets_list = section_bullets_combined if section_bullets_combined else None
            heading_out = heading_text if heading_text or not paragraph_texts else None

            sections.append({
                'heading': heading_out,
                'content': paragraph_texts if paragraph_texts else None,
                'bullets': bullets_list,
                'content_blocks': content_blocks
            })

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


# --- IO helpers ---
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


# --- Multithreading plumbing ---
_thread_local = threading.local()
_write_lock = threading.Lock()


def get_thread_session() -> requests.Session:
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests_session_with_retries()
        _thread_local.session = sess
    return sess


def fetch_and_save_item(item, idx: int, output_dir: str, delay: float) -> Dict:
    """
    Fetch a single item (dict or url string), parse it, save result or error, return details or error obj.
    """
    url = item.get("url") if isinstance(item, dict) else item
    if not url:
        return {"url": "", "error": "no url provided", "scrape_timestamp_utc": datetime.utcnow().isoformat() + "Z"}

    session = get_thread_session()
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

        with _write_lock:
            logging.info(f"Saved {outpath}")

        if delay and delay > 0:
            time.sleep(delay)

        return details

    except Exception as e:
        err_obj = {"url": url, "error": str(e), "scrape_timestamp_utc": datetime.utcnow().isoformat() + "Z"}
        slug = _make_slug_from_url(url) or f"topic-{idx}"
        outpath = os.path.join(output_dir, f"{slug}.error.json")
        save_json(err_obj, outpath)
        with _write_lock:
            logging.error(f"Failed to fetch or parse {url}: {e}")
        if delay and delay > 0:
            time.sleep(delay)
        return err_obj


def fetch_and_save_all(
    input_json: str,
    output_dir: str,
    aggregate_path: Optional[str] = None,
    delay: float = 0.1,
    log_level: str = "INFO",
    workers: int = 8,
    write_year_files: bool = True,
    year_dir_name: str = "by_year",
    wrap_year_meta: bool = False,
) -> List[Dict]:
    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO), format="%(levelname)s: %(message)s")
    items = load_input_json(input_json)
    os.makedirs(output_dir, exist_ok=True)

    results: List[Dict] = []
    total = len(items)
    logging.info(f"Found {total} input items to process (workers={workers})")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_and_save_item, item, idx, output_dir, delay): idx
            for idx, item in enumerate(items, start=1)
        }

        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            try:
                res = future.result()
            except Exception as e:
                res = {"url": "", "error": f"unhandled exception: {e}", "scrape_timestamp_utc": datetime.utcnow().isoformat() + "Z"}
                logging.error(f"Unhandled error for item {idx}: {e}")

            with _write_lock:
                results.append(res)
                processed = len(results)
                logging.info(f"Processed {processed}/{total} items")

    if aggregate_path:
        save_json(results, aggregate_path)
        logging.info(f"Wrote aggregate file to {aggregate_path}")

    # --- Year-wise grouping & output (single-threaded, after all tasks complete) ---
    if write_year_files:
        year_map: Dict[str, List[Dict]] = {}
        for r in results:
            if not isinstance(r, dict):
                continue
            pub = r.get("published_date") or ""
            # normalized date strings are often YYYY-MM-DD or ISO, so take first 4 characters if digits
            year = pub[:4] if len(pub) >= 4 and pub[:4].isdigit() else "unknown"
            year_map.setdefault(year, []).append(r)

        year_dir = os.path.join(output_dir, year_dir_name)
        os.makedirs(year_dir, exist_ok=True)
        for year, items_list in sorted(year_map.items(), reverse=True):
            year_path = os.path.join(year_dir, f"{year}.json")
            if wrap_year_meta:
                payload = {"year": year, "count": len(items_list), "items": items_list}
            else:
                payload = items_list
            save_json(payload, year_path)
            logging.info(f"Wrote year file {year_path} with {len(items_list)} items")

    return results


def main():
    parser = argparse.ArgumentParser(description="Fetch WebMD topic pages and save detailed JSON files per topic (multithreaded)")
    parser.add_argument("--input", "-i", default=INPUT_FILE, help="Input JSON file containing list of topic URLs")
    parser.add_argument("--output-dir", "-o", default=OUTPUT_DIR, help="Directory to write per-topic JSON files")
    parser.add_argument("--aggregate", "-a", default=None, help="Optional aggregated JSON file path to write all results")
    parser.add_argument("--delay", type=float, default=0.1, help="Seconds to wait between requests per thread")
    parser.add_argument("--log", default="info", help="Logging level (debug/info/warning/error)")
    parser.add_argument("--workers", type=int, default=8, help="Number of worker threads to use")
    parser.add_argument("--no-year-files", action="store_true", help="Do not write year-wise JSON files")
    parser.add_argument("--year-dir", default="by_year", help="Subdirectory name inside output-dir for year files")
    parser.add_argument("--wrap-year-meta", action="store_true", help="Wrap each year file in {year,count,items}")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    fetch_and_save_all(
        args.input,
        args.output_dir,
        aggregate_path=args.aggregate,
        delay=args.delay,
        log_level=args.log,
        workers=args.workers,
        # False for no writing year wise files.
        # write_year_files=not args.no_year_files,
        write_year_files=False,
        year_dir_name=args.year_dir,
        wrap_year_meta=args.wrap_year_meta,
    )


if __name__ == "__main__":
    main()

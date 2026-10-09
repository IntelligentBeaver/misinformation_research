#!/usr/bin/env python3
"""
WHO Fact-sheets extractor (single-run prototype).

- Collects links from https://www.who.int/news-room/fact-sheets
- Fetches each fact-sheet page and extracts structured sections
- Saves JSON at who/factsheets/<FirstLetterUpper>/<slug>.json
"""

import requests
from bs4 import BeautifulSoup, Tag, NavigableString
import re
import os
import json
import time
import unicodedata
from urllib.parse import urljoin
from pathlib import Path

BASE = "https://www.who.int"
INDEX_URL = "https://www.who.int/news-room/fact-sheets"
USER_AGENT = "Mozilla/5.0 (compatible; WHO-FS-Extractor/1.0; +verifact.scraper.bot)"

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

# Adjust politely
sleep_seconds = 0.1
request_timeout = 15.0
max_retries = 2

date_regex = re.compile(r"\b(?:\d{1,2}\s+[A-Za-z]+(?:\s+\d{4})?)\b")  # e.g. "28 June 2024" or "June 2024"

def slugify(text: str) -> str:
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text

def safe_get(url: str):
    for attempt in range(max_retries + 1):
        try:
            r = session.get(url, timeout=request_timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1.0 + attempt * 0.5)
                continue
            raise

def fetch_index_links(index_url=INDEX_URL):
    """Return list of absolute URLs for fact-sheet detail pages."""
    r = safe_get(index_url)
    soup = BeautifulSoup(r.text, "lxml")
    anchors = soup.find_all("a", href=True)
    links = set()
    for a in anchors:
        href = a["href"]
        if href.startswith("/news-room/fact-sheets/detail/"):
            full = urljoin(BASE, href)
            links.add(full)
    return sorted(links)

def extract_text_from_nodes(nodes):
    pieces = []
    for n in nodes:
        if isinstance(n, NavigableString):
            txt = str(n).strip()
            if txt:
                pieces.append(txt)
        elif isinstance(n, Tag):
            pieces.append(n.get_text(separator=" ", strip=True))
    return " ".join(pieces).strip()

def parse_fact_sheet(url):
    """Parse a WHO fact-sheet page into a dict structure."""
    r = safe_get(url)
    soup = BeautifulSoup(r.text, "lxml")

    # Try common selectors
    title_tag = soup.find(["h1"])
    title = title_tag.get_text(strip=True) if title_tag else None

    # published date: look for a short date-like text close to the title; fallback to any matching text
    published_date = None
    if title_tag:
        # search next siblings for date-like text
        for sib in title_tag.next_siblings:
            if isinstance(sib, NavigableString):
                s = sib.strip()
                if not s:
                    continue
                m = date_regex.search(s)
                if m:
                    published_date = m.group(0)
                    break
            elif isinstance(sib, Tag):
                txt = sib.get_text(" ", strip=True)
                m = date_regex.search(txt)
                if m:
                    published_date = m.group(0)
                    break

    if not published_date:
        # search whole document for first date-like text near top
        top_text = soup.get_text(" ", strip=True)[:400]
        m = date_regex.search(top_text)
        if m:
            published_date = m.group(0)

    # Locate the main content region. WHO pages often have a <main> element.
    main = soup.find("main")
    if not main:
        # fallback to body
        main = soup.body

    # Build sections: find all H2 (section headings). If none, fallback to grouping <h3> or paragraphs.
    sections = []
    heading_tags = main.find_all(["h2", "h3", "h4"])
    if not heading_tags:
        # fallback: group by top-level paragraphs
        body_text = main.get_text("\n", strip=True)
        sections.append({"heading": None, "content": [body_text] if body_text else None})
    else:
        for i, h in enumerate(heading_tags):
            heading = h.get_text(strip=True)
            # gather content until next heading tag at the same level
            contents = []
            tables = []
            images = []
            # iterate siblings after this heading
            for sib in h.next_siblings:
                if isinstance(sib, Tag) and sib.name in ["h2", "h3", "h4"]:
                    break
                if isinstance(sib, Tag):
                    if sib.name in ["p"]:
                        txt = sib.get_text(" ", strip=True)
                        if txt:
                            contents.append(txt)
                    elif sib.name in ["ul", "ol"]:
                        items = [li.get_text(" ", strip=True) for li in sib.find_all("li")]
                        if items:
                            if contents and isinstance(contents[-1], str):
                                contents[-1] = {"text": contents[-1], "bullets": items}
                            else:
                                contents.append({"text": None, "bullets": items})
                    elif sib.name == "table":
                        # convert table to list-of-rows (cells as text)
                        rows = []
                        for tr in sib.find_all("tr"):
                            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                            if cols:
                                rows.append(cols)
                        if rows:
                            tables.append(rows)
                    elif sib.name == "figure" or sib.find("img"):
                        imgs = sib.find_all("img")
                        for im in imgs:
                            src = im.get("src") or im.get("data-src")
                            if src:
                                images.append(urljoin(BASE, src))
                    # other tags: capture text
                    else:
                        txt = sib.get_text(" ", strip=True)
                        if txt:
                            contents.append(txt)
                elif isinstance(sib, NavigableString):
                    txt = sib.strip()
                    if txt:
                        contents.append(txt)
            section_obj = {
                "heading": heading,
                "content": contents if contents else None,
            }
            # If heading is "References", add a normalized references list of strings
            if heading and heading.strip().lower() == "references":
                refs = []
                for item in contents:
                    if isinstance(item, dict):
                        text = (item.get("text") or "").strip()
                        if text:
                            refs.append(text)
                        bullets = item.get("bullets") or []
                        if isinstance(bullets, str):
                            bullets = [bullets]
                        if isinstance(bullets, list):
                            refs.extend([b.strip() for b in bullets if b])
                    elif isinstance(item, str):
                        if item.strip():
                            refs.append(item.strip())
                section_obj["references"] = refs if refs else None
            if tables:
                section_obj["tables"] = tables
            if images:
                section_obj["images"] = images
            sections.append(section_obj)

    # collect other artifacts: PDFs and all images in main
    pdf_links = []
    for a in main.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            pdf_links.append(urljoin(BASE, href))

    # Collect references from any "References" section (if present)
    references = []
    for sec in sections:
        refs = sec.get("references")
        if isinstance(refs, list):
            references.extend([r for r in refs if r])

    if not references:
        references = None

    all_imgs = []
    for im in main.find_all("img"):
        src = im.get("src") or im.get("data-src") or im.get("data-lazy-src")
        if src:
            all_imgs.append(urljoin(BASE, src))

    # assemble output dictionary
    slug = slugify(title or url.rsplit("/", 1)[-1])
    first_letter = (slug[0].upper() if slug else "X")
    result = {
        "url": url,
        "title": title,
        "slug": slug,
        "published_date": published_date,
        # Add author as WHO itself for fact sheets
        "author": "World Health Organization (WHO)",
        "first_letter": first_letter,
        "sections": sections,
        "references": references,
        "pdfs": list(sorted(set(pdf_links))),
        "images": list(sorted(set(all_imgs))),
        "tags": ["Health Fact", slug, title, "WHO"],
        "scrape_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    return result

def save_json_record(record, base_dir="../../../storage/who/factsheets"):
    # letter = record.get("first_letter", "X")
    slug = record.get("slug", "unknown")
    folder = Path(base_dir)
    folder.mkdir(parents=True, exist_ok=True)
    filepath = folder / f"{slug}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return str(filepath)

def main(limit=None):
    print("Fetching WHO fact sheet index...")
    links = fetch_index_links()
    print(f"Found {len(links)} fact-sheet links (unique).")
    if limit:
        links = links[:limit]
        print(f"Limiting to first {limit} links.")
    for i, link in enumerate(links, 1):
        try:
            print(f"[{i}/{len(links)}] Fetching: {link}")
            record = parse_fact_sheet(link)
            path = save_json_record(record)
            print(f"  → saved: {path}")
        except Exception as e:
            print(f"  ! error parsing {link}: {e}")
        time.sleep(sleep_seconds)

if __name__ == "__main__":
    # Example: to only process 1 link while testing, set limit=1
    main()

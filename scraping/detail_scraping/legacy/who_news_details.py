import json
import os
import argparse
import time

import requests, re
from bs4 import BeautifulSoup, Tag, NavigableString
from urllib.parse import urljoin
from datetime import datetime, timedelta

HEADERS = {"User-Agent": "Mozilla/5.0 (WHO-scraper/1.0)"}
HEADLINES_DIR = "../../../storage/who/headlines"
OUTPUT_DIR = "../../../storage/who/news"
NEW_ARTICLES_DIR = "../../../storage/who/new_articles"  # For periodic runs

SAVE_EVERY = 5  # save every 5 articles

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(NEW_ARTICLES_DIR, exist_ok=True)


def parse_article_sections(article_div, base_url, initial_heading=None):
    """
    Parse an article div into sections with heading, content as list of paragraphs,
    and attach any list to the preceding paragraph.
    """
    sections = []

    # Find heading tags (h2, h3, h4) as section markers
    heading_tags = article_div.find_all(["h2", "h3", "h4"])

    def append_list_or_text(contents, items):
        if contents and isinstance(contents[-1], str):
            contents[-1] = {"text": contents[-1], "bullets": items}
        else:
            contents.append({"text": None, "bullets": items})

    def collect_from_node(node, contents):
        if isinstance(node, Tag):
            if node.name == "p":
                text = node.get_text(" ", strip=True)
                if text:
                    contents.append(text)
            elif node.name in ["ul", "ol"]:
                items = [li.get_text(" ", strip=True) for li in node.find_all("li")]
                if items:
                    append_list_or_text(contents, items)
            elif node.name not in ["h2", "h3", "h4"]:
                text = node.get_text(" ", strip=True)
                if text:
                    contents.append(text)
        elif isinstance(node, NavigableString):
            text = node.strip()
            if text:
                contents.append(text)

    if not heading_tags:
        # fallback: treat entire content as one section
        contents = []
        for el in article_div.children:
            collect_from_node(el, contents)
        sections.append({"heading": None, "content": contents if contents else None})
    else:
        # collect content before the first heading
        first_heading = heading_tags[0]
        lead_contents = []
        for el in article_div.children:
            if el == first_heading:
                break
            collect_from_node(el, lead_contents)
        if lead_contents:
            sections.append({"heading": initial_heading, "content": lead_contents})

        for i, h in enumerate(heading_tags):
            heading = h.get_text(strip=True)
            content_blocks = []
            tables = []
            images = []

            # iterate siblings until next heading at same or higher level
            for sib in h.next_siblings:
                if isinstance(sib, Tag) and sib.name in ["h2", "h3", "h4"]:
                    break
                if isinstance(sib, Tag):
                    if sib.name == "table":
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
                                images.append(urljoin(base_url, src))
                    else:
                        collect_from_node(sib, content_blocks)
                elif isinstance(sib, NavigableString):
                    collect_from_node(sib, content_blocks)

            # Build the section object
            section_obj = {
                "heading": heading,
                "content": content_blocks if content_blocks else None,
            }
            if tables:
                section_obj["tables"] = tables
            if images:
                section_obj["images"] = images

            sections.append(section_obj)

    return sections

def load_existing_items(filepath):
    if not os.path.exists(filepath):
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def load_urls_from_year_file(filepath, min_year=2020, recent_days=None):
    """Load URLs from year file, optionally filtered by recent date.
    
    Args:
        filepath: Path to the year JSON file
        min_year: Minimum year to include
        recent_days: If set, only include articles from last N days
    """
    with open(filepath, "r", encoding="utf-8") as f:
        items = json.load(f)

    cutoff_date = None
    if recent_days:
        cutoff_date = (datetime.now() - timedelta(days=recent_days)).date()

    urls = []
    for item in items:
        date = item.get("date")
        if not date:
            continue

        year = int(date[:4])
        if year < min_year:
            continue

        # Filter by recent date if specified
        if cutoff_date:
            try:
                article_date = datetime.strptime(date, "%Y-%m-%d").date()
                if article_date < cutoff_date:
                    continue
            except:
                continue

        urls.append(item["url"])

    return urls

def parse_date_str(s):
    if not s:
        return None
    s = s.strip()
    fmts = ["%d %B %Y", "%d %b %Y", "%d %B, %Y", "%d %b, %Y"]
    for f in fmts:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except Exception:
            pass
    # try zfill day
    parts = s.split()
    if len(parts) >= 3:
        day = parts[0].zfill(2)
        try:
            return datetime.strptime(f"{day} {parts[1]} {parts[2]}", "%d %B %Y").date().isoformat()
        except Exception:
            pass
    return None

def extract_media_contacts(block):
    # `block` is a BeautifulSoup element containing the media contacts block
    contacts = []
    if not block:
        return contacts
    # naive approach: split by name headings (they appear as plain text blocks)
    # Better approach: find sequences of <p> or block-level elements.
    # Here we look for groupings: name (text), role (text), then contact lines
    text_lines = [t.get_text(" ", strip=True) for t in block.find_all(recursive=False)]
    # fallback: parse using regex block search
    # We'll attempt a conservative parse: find lines that look like emails, phones and names
    raw = block.get_text("\n", strip=True)
    # split into persons by double newlines or by occurrence of 'Spokesperson'/'Communication Officer'
    persons = re.split(r'\n{2,}', raw)
    for p in persons:
        p = p.strip()
        if not p:
            continue
        # find emails and phones
        emails = re.findall(r'[\w\.-]+@[\w\.-]+', p)
        phones = re.findall(r'(\+?\d[\d\s\-\(\)]+)', p)
        # first line as name (approx)
        lines = [l.strip() for l in p.splitlines() if l.strip()]
        name = lines[0] if lines else None
        role = lines[1] if len(lines) > 1 else None
        contacts.append({
            "name": name,
            "role": role,
            "emails": emails or None,
            "phones": phones or None,
            "raw": p
        })
    return contacts

def scrape_who_news_item(url):
    """
    Robust scraper for WHO news item pages.
    - Uses the <article class="sf-detail-body-wrapper"> (or the article tag) as the main content area.
    - Extracts title, date, type, location, reading_time (value after 'Reading time:'), lead, content_html, content_text,
      bullets, references (from content), corrigendum, media contacts, topics, images and meta.
    - Avoids capturing site navigation by limiting to the article container.
    """
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    base = resp.url
    soup = BeautifulSoup(resp.text, "html.parser")

    # Title
    title_el = soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else None

    # Date (from header)
    date_el = soup.select_one(".sf-item-header-wrapper span.timestamp") or soup.select_one("span.timestamp")
    date = parse_date_str(date_el.get_text(strip=True)) if date_el else None

    # Header tags in the title block (type + location)
    header_tag_items = [t.get_text(strip=True) for t in soup.select(".sf-item-header-wrapper .sf-tags-list-item")]
    header_tag_items = [t for t in header_tag_items if t]

    # Type: prefer header tags in order (e.g., News release, Note for Media)
    item_type = header_tag_items[0] if header_tag_items else None

    # Location/dateliner: prefer header tags, else look for uppercase token preceding first paragraph
    location = None
    header_locations = header_tag_items[1:] if len(header_tag_items) > 1 else []
    if header_locations:
        location = " | ".join(header_locations)
    # prefer article first paragraph (we will find article below)
    #  Change this later if WHO changes
    article = soup.select_one("article.sf-detail-body-wrapper, article, div.sf-detail-body-wrapper")
    first_para = None
    if article:
        first_para = article.select_one("p")
    else:
        first_para = soup.select_one("div.main-content p, article p, .sf-content-block p")

    if first_para:
        sibs = list(first_para.previous_siblings)
        for s in reversed(sibs):
            txt = getattr(s, "get_text", lambda **k: str(s))().strip()
            if txt and txt.upper() == txt and 1 <= len(txt) < 80:
                location = txt
                break

    # Lead (first meaningful paragraph inside article)
    lead = None
    if first_para:
        lead = first_para.get_text(strip=True)

    # Main article container: prioritize the article element to avoid menus/nav
    # content_container = article or soup.select_one("div.sf-content-block, div.content, div.main-content, div.page__content, main")
    content_container = soup.select_one("article.sf-detail-body-wrapper div")
    header_h2 = soup.select_one("div.sf-item-header-wrapper h2")
    initial_heading = header_h2.get_text(strip=True) if header_h2 else None
    sections = parse_article_sections(content_container, resp.url, initial_heading=initial_heading) if content_container else []

    if not content_container:
        content_container = soup  # fallback, but we prefer article

    # CONTENT: use inner HTML of the article container (not entire page)
    # decode_contents() returns the HTML inside the tag (no wrapper)
    content_text = content_container.get_text("\n", strip=True) if content_container else None

    # References / related links inside content (anchor tags inside content only)
    references = []
    if content_container:
        for a in content_container.select("a[href]"):
            href = a["href"]
            full = urljoin(base, href)
            text = a.get_text(" ", strip=True)
            references.append({"text": text, "url": full})

    # Media contacts: find a header that mentions "Media" and grab following siblings until next section header
    contacts = []
    mc_header = soup.find(lambda tag: tag.name in ("h2", "h3", "strong", "p")
                          and re.search(r"\bmedia\b", tag.get_text(), re.I))
    if mc_header:
        block = mc_header.find_next_sibling()
        nodes = []
        cur = block
        # collect until next h2/h3/hr or until we've collected a few nodes
        while cur and not (cur.name in ("h2", "h3") or cur.name == "hr"):
            nodes.append(cur)
            cur = cur.find_next_sibling()
        if nodes:
            bs = BeautifulSoup("<div>" + "".join(str(n) for n in nodes) + "</div>", "html.parser")
            contacts = extract_media_contacts(bs.div)

    # Topics: keep only the item type (e.g., News release)
    topics = item_type

    # Author: use media contact names (comma-separated if multiple)
    author = None
    if contacts:
        names = [c.get("name") for c in contacts if c.get("name")]
        author = ", ".join(names) if names else None
    
    # Default to WHO if no author or if author looks like malformed data
    if not author:
        author = "WHO"
    else:
        # Validate author - replace with WHO if it looks like malformed data
        author_stripped = author.strip()
        
        # Patterns indicating malformed data:
        # - Date formats: 2024-01-29, 29/01/2024, etc.
        # - Time formats: 14:30, 23:59
        # - URLs or emails
        # - Generic placeholder text
        # - Mostly numeric content
        # - Too short (< 2 chars) or suspiciously long (> 150 chars)
        
        is_invalid = (
            re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', author_stripped) or  # Date: YYYY-MM-DD
            re.search(r'\d{1,2}[-/]\d{1,2}[-/]\d{4}', author_stripped) or  # Date: DD-MM-YYYY
            re.search(r'\d{2}:\d{2}', author_stripped) or  # Time: HH:MM
            re.search(r'@[\w\.-]+\.\w+', author_stripped) or  # Email domain
            re.search(r'https?://', author_stripped, re.I) or  # URL
            re.search(r'^(contact|media|information|unknown|n/a|none)$', author_stripped, re.I) or  # Generic text
            len(author_stripped) < 2 or  # Too short
            len(author_stripped) > 150 or  # Suspiciously long
            sum(c.isdigit() for c in author_stripped) / max(len(author_stripped), 1) > 0.5  # >50% digits
        )
        
        if is_invalid:
            author = "WHO"

    # Images: try og:image first, then data-image/style/src within article/content
    og_image = None
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        og_image = urljoin(base, og["content"])

    images = []
    # prefer images inside content_container/article
    if content_container:
        for d in content_container.select("div.background-image, .hero img, figure img, img"):
            di = d.get("data-image") if d.has_attr("data-image") else None
            if di:
                images.append(urljoin(base, di))
                continue
            if d.name == "img" and d.get("src"):
                images.append(urljoin(base, d.get("src")))
                continue
            style = d.get("style") or ""
            m = re.search(r'url\((?:&quot;|")?(.*?)(?:&quot;|")?\)', style)
            if m:
                images.append(urljoin(base, m.group(1)))
    # fall back to og:image if nothing found inside content
    if not images and og_image:
        images = [og_image]

    result = {
        "url": base,
        "title": title,
        "published_date": date,
        "author": author,
        "medically_reviewed_by": "World Health Organization (WHO)",
        "topics": topics,
        "location": location,
        "sections": sections,
        "references": references,
        "images": images,
        "tags": [t for t in ["WHO", title, item_type] if t],
        "scrape_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "first_seen_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())  # When first discovered
    }
    return result

def main():
    parser = argparse.ArgumentParser(description="Scrape WHO news articles")
    parser.add_argument(
        "--recent-days",
        type=int,
        default=None,
        help="Only process articles from the last N days (for periodic runs)"
    )
    parser.add_argument(
        "--min-year",
        type=int,
        default=2006,
        help="Minimum year to process (default: 2006)"
    )
    args = parser.parse_args()

    run_timestamp = time.strftime("%Y%m%d_%H%M%S")
    new_articles_this_run = []
    total_new = 0
    total_skipped = 0

    print(f"\n{'='*60}")
    print(f"WHO News Scraper - Run started at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if args.recent_days:
        print(f"Mode: PERIODIC (last {args.recent_days} days only)")
        cutoff = datetime.now() - timedelta(days=args.recent_days)
        print(f"Cutoff date: {cutoff.strftime('%Y-%m-%d')}")
    else:
        print(f"Mode: FULL (all articles from {args.min_year} onwards)")
    print(f"{'='*60}\n")

    for filename in sorted(os.listdir(HEADLINES_DIR)):
        if not filename.endswith(".json"):
            continue

        year = filename.replace(".json", "")
        input_path = os.path.join(HEADLINES_DIR, filename)
        output_path = os.path.join(OUTPUT_DIR, f"{year}.json")

        print(f"\n📂 Processing year {year}...")

        urls = load_urls_from_year_file(input_path, min_year=args.min_year, recent_days=args.recent_days)

        if not urls:
            print(f"  No URLs to process for {year}")
            continue

        year_results = load_existing_items(output_path)
        existing_urls = {item["url"] for item in year_results if "url" in item}
        year_new_count = 0
        year_skip_count = 0

        for i, url in enumerate(urls, 1):
            print(f"  [{i}/{len(urls)}] {url}")

            if url in existing_urls:
                print("  ⏭️  Skipped (already scraped)")
                total_skipped += 1
                year_skip_count += 1
                continue

            try:
                data = scrape_who_news_item(url)
                if not data:
                    continue

                year_results.append(data)
                existing_urls.add(url)
                new_articles_this_run.append(data)
                year_new_count += 1
                total_new += 1

                print(f"  ✅ NEW article scraped")

                if len(year_results) % SAVE_EVERY == 0:
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(year_results, f, ensure_ascii=False, indent=2)
                    print("  💾 Progress saved")

            except Exception as e:
                print(f"  ❌ Error: {e}")

            time.sleep(0.1)

        # Save year file
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(year_results, f, ensure_ascii=False, indent=2)

        print(f"\n  Year {year} summary: {year_new_count} new, {year_skip_count} skipped, {len(year_results)} total")

    # Save new articles from this run to a separate file
    if new_articles_this_run:
        new_articles_file = os.path.join(NEW_ARTICLES_DIR, f"new_articles_{run_timestamp}.json")
        with open(new_articles_file, "w", encoding="utf-8") as f:
            json.dump(new_articles_this_run, f, ensure_ascii=False, indent=2)
        print(f"\n📄 Saved {len(new_articles_this_run)} new articles to: {new_articles_file}")

    # Print final summary
    print(f"\n{'='*60}")
    print(f"RUN SUMMARY")
    print(f"{'='*60}")
    print(f"✅ New articles discovered: {total_new}")
    print(f"⏭️  Already existing (skipped): {total_skipped}")
    print(f"📊 Total processed: {total_new + total_skipped}")
    if args.recent_days:
        print(f"🔄 Periodic mode: last {args.recent_days} days")
    print(f"⏰ Completed at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()

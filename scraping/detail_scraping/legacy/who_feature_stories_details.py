import json
import os
import argparse
import time
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests
from bs4 import BeautifulSoup, Tag, NavigableString

# Configuration
HEADLINES_DIR = "../../../storage/who/feature_stories_headlines"
OUTPUT_DIR = "../../../storage/who/feature_stories_news"
SAVE_EVERY = 5  # save every 5 articles

HEADERS = {"User-Agent": "Mozilla/5.0 (WHO-scraper/1.0)"}

# Thread-safe locks
existing_urls_lock = Lock()
articles_by_year_lock = Lock()

os.makedirs(OUTPUT_DIR, exist_ok=True)


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


def parse_date_str(s):
    if not s:
        return None
    s = s.strip()
    fmts = ["%d %B %Y", "%d %b %Y", "%d %B, %Y", "%d %b, %Y", "%B %d, %Y", "%b %d, %Y"]
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


def scrape_feature_story_item(url):
    """
    Robust scraper for WHO feature story pages.
    - Uses the <article class="sf-detail-body-wrapper"> as the main content area.
    - Extracts title, date, type, location, lead, sections, references, media contacts, topics, images and meta.
    """
    try:
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

        # Type: prefer header tags in order (e.g., Feature Story)
        item_type = header_tag_items[0] if header_tag_items else "Feature Story"

        # Location: prefer header tags
        location = None
        header_locations = header_tag_items[1:] if len(header_tag_items) > 1 else []
        if header_locations:
            location = " | ".join(header_locations)

        # Main article container: prioritize the article element to avoid menus/nav
        article = soup.select_one("article.sf-detail-body-wrapper, article, div.sf-detail-body-wrapper")
        
        # Lead (first meaningful paragraph inside article)
        lead = None
        first_para = None
        if article:
            first_para = article.select_one("p")
            if first_para:
                lead = first_para.get_text(strip=True)

        # Content container
        content_container = soup.select_one("article.sf-detail-body-wrapper div")
        header_h2 = soup.select_one("div.sf-item-header-wrapper h2")
        initial_heading = header_h2.get_text(strip=True) if header_h2 else None
        sections = parse_article_sections(content_container, resp.url, initial_heading=initial_heading) if content_container else []

        if not content_container:
            content_container = soup  # fallback

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

        # Topics: use the item type
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
            "tags": [t for t in ["WHO", "Feature Story", title, item_type] if t],
            "scrape_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "first_seen_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        return result
    
    except Exception as e:
        print(f"    ❌ Error scraping {url}: {e}")
        return None


def extract_year_from_date(date_str):
    """Extract year from date string."""
    if not date_str:
        return None
    import re
    if match:
        return int(match.group(0))
    return None


def load_headlines_from_year_files(headlines_dir):
    """Load all headlines from year JSON files."""
    all_headlines = {}
    
    if not os.path.exists(headlines_dir):
        print(f"❌ Headlines directory not found: {headlines_dir}")
        return all_headlines
    
    for filename in sorted(os.listdir(headlines_dir), reverse=True):
        if filename.endswith('.json') and filename[0].isdigit():
            year = filename.replace('.json', '')
            filepath = os.path.join(headlines_dir, filename)
            
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    headlines = json.load(f)
                    all_headlines[year] = headlines
                    print(f"📂 Loaded {len(headlines)} headlines from {filename}")
            except Exception as e:
                print(f"❌ Error loading {filename}: {e}")
    
    return all_headlines


def process_year_articles(year, headlines, articles_by_year, existing_urls, args):
    """
    Process all articles for a given year.
    This function runs in a separate thread for each year.
    
    Args:
        year: Year string (e.g., '2025')
        headlines: List of headline dictionaries for this year
        articles_by_year: Shared dict of articles by year
        existing_urls: Shared set of existing URLs
        args: Arguments object with min_year, max_articles, etc.
    
    Returns:
        Dictionary with stats: {new_count, skipped_count, processed, year_articles}
    """
    year_int = int(year) if year != 'unknown' else 0
    
    # Skip if below min year
    if year_int > 0 and year_int < args.min_year:
        print(f"📂 Skipping year {year} (below minimum {args.min_year})")
        return {
            "year": year,
            "new_count": 0,
            "skipped_count": len(headlines),
            "processed": 0,
            "year_articles": []
        }
    
    print(f"📂 Processing year {year} ({len(headlines)} headlines)...")
    
    new_count = 0
    skipped_count = 0
    processed = 0
    year_articles = []
    
    for i, headline in enumerate(headlines, 1):
        url = headline.get('url')
        
        # Thread-safe check for existing URL
        with existing_urls_lock:
            if url in existing_urls:
                skipped_count += 1
                print(f"  [{i}/{len(headlines)}] ⏭️  Already extracted: {headline.get('title', 'Unknown')[:50]}")
                continue
        
        # Check max articles limit
        if args.max_articles and processed >= args.max_articles:
            print(f"  ⏹️  Reached max articles limit ({args.max_articles})")
            break
        
        print(f"  [{i}/{len(headlines)}] 🔄 Scraping {headline.get('title', 'Unknown')[:50]}...")
        
        # Scrape article details
        article_data = scrape_feature_story_item(url)
        
        if article_data:
            year_articles.append(article_data)
            
            # Thread-safe update of existing_urls
            with existing_urls_lock:
                existing_urls.add(url)
            
            new_count += 1
            processed += 1
            print(f"    ✅ Scraped successfully")
            
            # Save progress
            if len(year_articles) % SAVE_EVERY == 0:
                with articles_by_year_lock:
                    filepath = os.path.join(OUTPUT_DIR, f"{year}.json")
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(year_articles, f, ensure_ascii=False, indent=2)
                print(f"    💾 Progress saved ({len(year_articles)} articles)")
        
        time.sleep(0.5)  # Be nice to the server
    
    return {
        "year": year,
        "new_count": new_count,
        "skipped_count": skipped_count,
        "processed": processed,
        "year_articles": year_articles
    }


def save_articles_by_year(articles_by_year, output_dir):
    """Save articles grouped by year to separate JSON files."""
    total_saved = 0
    for year in sorted(articles_by_year.keys(), reverse=True):
        articles = articles_by_year[year]
        filepath = os.path.join(output_dir, f"{year}.json")
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        
        print(f"  ✅ Saved {len(articles)} articles to {year}.json")
        total_saved += len(articles)
    
    return total_saved


def main():
    parser = argparse.ArgumentParser(description="Scrape WHO Feature Story article details")
    parser.add_argument(
        "--min-year",
        type=int,
        default=2000,
        help="Minimum year to extract (default: 2000)"
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=None,
        help="Maximum number of articles to scrape per year (default: all)"
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Number of threads to use (default: 4)"
    )
    parser.add_argument(
        "--recent-days",
        type=int,
        default=None,
        help="Only process articles from the last N days (for periodic runs)"
    )
    args = parser.parse_args()
    
    print("="*60)
    print("WHO Feature Stories Details Scraper")
    print("="*60)
    print(f"Min year: {args.min_year}")
    if args.max_articles:
        print(f"Max articles per year: {args.max_articles}")
    if args.recent_days:
        print(f"Recent days: {args.recent_days}")
    print(f"Threads: {args.threads}")
    
    # Load existing detailed articles
    print("\n📂 Loading existing articles...")
    articles_by_year = {}
    existing_urls = set()
    
    for filename in os.listdir(OUTPUT_DIR):
        if filename.endswith('.json') and filename[0].isdigit():
            year = filename.replace('.json', '')
            filepath = os.path.join(OUTPUT_DIR, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    articles = json.load(f)
                    articles_by_year[year] = articles
                    for article in articles:
                        if 'url' in article:
                            existing_urls.add(article['url'])
            except:
                pass
    
    if existing_urls:
        print(f"  Found {len(existing_urls)} existing articles to skip")
    
    # Load headlines from feature_stories_headlines directory
    print(f"\n📄 Loading headlines from {HEADLINES_DIR}...")
    headlines_by_year = load_headlines_from_year_files(HEADLINES_DIR)
    
    if not headlines_by_year:
        print("❌ No headlines found!")
        return
    
    # Filter by recent days if specified
    if args.recent_days:
        cutoff_date = (datetime.now() - timedelta(days=args.recent_days)).date()
        print(f"Filtering for articles after {cutoff_date}")
        
        filtered_headlines = {}
        for year, headlines in headlines_by_year.items():
            filtered = []
            for h in headlines:
                date_str = h.get('date')
                if date_str:
                    try:
                        article_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                        if article_date >= cutoff_date:
                            filtered.append(h)
                    except:
                        pass
            if filtered:
                filtered_headlines[year] = filtered
        
        headlines_by_year = filtered_headlines
        print(f"Filtered to {sum(len(h) for h in headlines_by_year.values())} recent articles")
    
    # Process headlines and scrape articles using ThreadPoolExecutor
    total_headlines = sum(len(h) for h in headlines_by_year.values())
    print(f"\n📊 Total headlines to process: {total_headlines}")
    
    total_new = 0
    total_skipped = 0
    
    # Submit tasks to thread pool
    print(f"\n🧵 Starting {args.threads} threads...\n")
    
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        # Create a dictionary of futures for each year
        future_to_year = {}
        
        for year in sorted(headlines_by_year.keys(), reverse=True):
            headlines = headlines_by_year[year]
            
            # Initialize year in articles_by_year if not present
            if year not in articles_by_year:
                articles_by_year[year] = []
            
            # Submit task to thread pool
            future = executor.submit(
                process_year_articles,
                year,
                headlines,
                articles_by_year,
                existing_urls,
                args
            )
            future_to_year[future] = year
        
        # Process completed tasks
        for future in as_completed(future_to_year):
            year = future_to_year[future]
            try:
                result = future.result()
                
                # Update articles for this year
                articles_by_year[year] = result["year_articles"]
                
                # Accumulate stats
                total_new += result["new_count"]
                total_skipped += result["skipped_count"]
                
                print(f"\n✅ Year {year} completed: "
                      f"{result['new_count']} new, "
                      f"{result['skipped_count']} skipped")
                
            except Exception as e:
                print(f"❌ Error processing year {year}: {e}")
    
    # Save all articles
    print("\n💾 Saving articles by year...")
    total_saved = save_articles_by_year(articles_by_year, OUTPUT_DIR)
    
    # Print summary
    print("\n" + "="*60)
    print("📊 SUMMARY")
    print("="*60)
    print(f"New articles scraped: {total_new}")
    print(f"Already existing (skipped): {total_skipped}")
    print(f"Total articles in files: {total_saved}")
    print(f"Years covered: {', '.join(str(y) for y in sorted(articles_by_year.keys(), reverse=True) if y != 'unknown')}")
    for year in sorted(articles_by_year.keys(), reverse=True):
        print(f"  {year}: {len(articles_by_year[year])} articles")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()

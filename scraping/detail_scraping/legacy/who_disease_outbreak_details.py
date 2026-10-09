import json
import os
import argparse
import time
import re
from datetime import datetime
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests
from bs4 import BeautifulSoup, Tag, NavigableString

# Configuration
HEADLINES_DIR = "../../../storage/who/disease_outbreak_headlines"
OUTPUT_DIR = "../../../storage/who/disease_outbreak_news"
SAVE_EVERY = 5  # save every 3 articles

HEADERS = {"User-Agent": "Mozilla/5.0 (WHO-scraper/1.0)"}

# Thread-safe locks
existing_urls_lock = Lock()
articles_by_year_lock = Lock()

os.makedirs(OUTPUT_DIR, exist_ok=True)


def fetch_with_retries(url, headers, timeout=15, max_retries=5, backoff_base=2.0, backoff_min=2.0, backoff_max=60.0):
    """Fetch a URL with retry/backoff support for 429 and transient errors."""
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        sleep_for = float(retry_after)
                    except ValueError:
                        sleep_for = backoff_min
                else:
                    sleep_for = min(backoff_max, max(backoff_min, backoff_base ** (attempt - 1)))
                time.sleep(sleep_for)
                last_err = requests.HTTPError(f"429 Too Many Requests for url: {url}")
                continue
            resp.raise_for_status()
            return resp
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last_err = e
            if attempt >= max_retries:
                break
            sleep_for = min(backoff_max, max(backoff_min, backoff_base ** (attempt - 1)))
            time.sleep(sleep_for)
    if last_err:
        raise last_err
    raise requests.HTTPError(f"Failed to fetch url: {url}")


def load_existing_items(filepath):
    """Load existing articles from a year JSON file."""
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_media_contacts(block):
    """Extract media contacts from a BeautifulSoup element block."""
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


def extract_year_from_date(date_str):
    """Extract year from date string."""
    if not date_str:
        return None
    match = re.search(r'\b(19|20)\d{2}\b', str(date_str))
    if match:
        return int(match.group(0))
    return None


def parse_article_sections(article_div, base_url):
    """
    Parse disease outbreak article into sections.
    Handles the specific DON structure:
    - <h3 class="don-section">Heading</h3>
    - <div class="don-content">Content</div>
    
    Skips:
    - <div class="arrowed-link">
    - <div class="don-images">
    """
    sections = []

    if not article_div:
        return sections

    def append_list_or_text(contents, items):
        """Attach list to previous paragraph or create standalone list."""
        if contents and isinstance(contents[-1], str):
            contents[-1] = {"text": contents[-1], "bullets": items}
        else:
            contents.append({"text": None, "bullets": items})

    def collect_from_node(node, contents):
        """Recursively collect text content from a node."""
        if isinstance(node, Tag):
            if node.name == "p":
                text = node.get_text(" ", strip=True)
                if text:
                    contents.append(text)
            elif node.name in ["ul", "ol"]:
                items = [li.get_text(" ", strip=True) for li in node.find_all("li")]
                if items:
                    append_list_or_text(contents, items)
            elif node.name not in ["h3", "div"]:
                text = node.get_text(" ", strip=True)
                if text and len(text) > 3:
                    contents.append(text)
        elif isinstance(node, NavigableString):
            text = node.strip()
            if text and len(text) > 3:
                contents.append(text)

    # Find all h3 elements with class "don-section"
    heading_tags = article_div.find_all("h3", class_="don-section")

    if not heading_tags:
        # Fallback: treat entire content as one section
        contents = []
        for el in article_div.children:
            # Skip unwanted divs
            if isinstance(el, Tag):
                if el.get("class"):
                    classes = " ".join(el.get("class", []))
                    if "arrowed-link" in classes or "don-images" in classes:
                        continue
            collect_from_node(el, contents)
        if contents:
            sections.append({"heading": None, "content": contents})
    else:
        # Process each heading and its corresponding content div
        for heading_tag in heading_tags:
            heading = heading_tag.get_text(strip=True)
            content_blocks = []
            tables = []
            images = []

            # Find the next <div class="don-content"> sibling
            content_div = heading_tag.find_next("div", class_="don-content")
            
            if content_div:
                # Extract content from the don-content div
                for child in content_div.children:
                    # Skip unwanted elements
                    if isinstance(child, Tag):
                        if child.get("class"):
                            classes = " ".join(child.get("class", []))
                            if "arrowed-link" in classes or "don-images" in classes:
                                continue
                        
                        if child.name == "table":
                            rows = []
                            for tr in child.find_all("tr"):
                                cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                                if cols:
                                    rows.append(cols)
                            if rows:
                                tables.append(rows)
                        elif child.name == "figure" or child.find("img"):
                            imgs = child.find_all("img")
                            for im in imgs:
                                src = im.get("src") or im.get("data-src")
                                if src:
                                    images.append(urljoin(base_url, src))
                        elif child.name == "div":
                            # Recursively process nested divs (e.g., footnotes)
                            for nested in child.children:
                                collect_from_node(nested, content_blocks)
                        else:
                            collect_from_node(child, content_blocks)
                    else:
                        collect_from_node(child, content_blocks)

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


def scrape_disease_outbreak_article(url):
    """
    Scrape a disease outbreak news article.
    Extracts sections using the don-revamp structure.
    """
    try:
        resp = fetch_with_retries(url, headers=HEADERS, timeout=15)
        base = resp.url
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Extract title
        title_el = soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else None
        
        # Extract date
        date_el = soup.select_one(".sf-item-header-wrapper span.timestamp") or soup.select_one("span.timestamp")
        date_text = date_el.get_text(strip=True) if date_el else None
        date = parse_date_str(date_text) if date_text else None
        
        # Find main article container
        article_container = soup.select_one("article.sf-detail-body-wrapper.don-revamp")
        
        if not article_container:
            # Fallback to generic article container
            article_container = soup.select_one("article.sf-detail-body-wrapper")
        
        # Extract sections
        sections = parse_article_sections(article_container, base)
        
        # # Extract first paragraph as lead
        # lead = None
        # first_p = article_container.find('p') if article_container else None
        # if first_p:
        #     lead = first_p.get_text(strip=True)
        
        # Extract references
        references = []
        if article_container:
            for a in article_container.select("a[href]"):
                href = a["href"]
                full = urljoin(base, href)
                text = a.get_text(strip=True)
                if text and not text.startswith('#'):
                    references.append({"text": text, "url": full})
        
        # Extract images
        images = []
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            images.append(urljoin(base, og["content"]))
        
        # Extract media contacts and author
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
        
        result = {
            "url": base,
            "title": title,
            "published_date": date,
            "author": author,
            "medically_reviewed_by": "World Health Organization (WHO)",
            "topics": "Disease Outbreak",
            "sections": sections,
            "references": references,
            "images": images,
            "tags": [t for t in ["WHO", "Disease Outbreak", title] if t],
            "scraped_at": datetime.now().isoformat(),
            "first_seen_utc": datetime.utcnow().isoformat()
        }
        return result
    
    except Exception as e:
        print(f"    ❌ Error scraping {url}: {e}")
        return None


def parse_date_str(s):
    """Parse date string to ISO format."""
    if not s:
        return None
    s = s.strip()
    
    formats = [
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.date().isoformat()
        except:
            continue
    
    return s


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
        
        # Check max articles limit (shared across threads, but we check per thread)
        if args.max_articles and processed >= args.max_articles:
            print(f"  ⏹️  Reached max articles limit ({args.max_articles})")
            break
        
        print(f"  [{i}/{len(headlines)}] 🔄 Scraping {headline.get('title', 'Unknown')[:50]}...")
        
        # Scrape article details
        article_data = scrape_disease_outbreak_article(url)
        
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


def load_headlines_from_year_files(headlines_dir):
    """Load all headlines from year JSON files in disease_outbreak directory."""
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
    parser = argparse.ArgumentParser(description="Scrape WHO Disease Outbreak News article details")
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
        help="Maximum number of articles to scrape (default: all)"
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Number of threads to use (default: 4)"
    )
    args = parser.parse_args()
    
    print("="*60)
    print("WHO Disease Outbreak News Details Scraper")
    print("="*60)
    print(f"Min year: {args.min_year}")
    if args.max_articles:
        print(f"Max articles: {args.max_articles}")
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
    
    # Load headlines from disease_outbreak directory
    print(f"\n📄 Loading headlines from {HEADLINES_DIR}...")
    headlines_by_year = load_headlines_from_year_files(HEADLINES_DIR)
    
    if not headlines_by_year:
        print("❌ No headlines found!")
        return
    
    # Process headlines and scrape articles using ThreadPoolExecutor
    total_articles = sum(len(h) for h in headlines_by_year.values())
    print(f"\n📊 Total headlines to process: {total_articles}")
    
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

import json
import os
import time
import argparse
from datetime import datetime
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from bs4 import BeautifulSoup

# Configuration
BASE_URL = "https://www.who.int/emergencies/disease-outbreak-news"
OUTPUT_DIR = "../../../storage/who/disease_outbreak_headlines"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def setup_driver():
    """Setup Selenium WebDriver with headless Chrome."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Run in background
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    # Try to use Chrome, fallback to Edge if not available
    try:
        driver = webdriver.Chrome(options=chrome_options)
        return driver
    except Exception as e:
        print(f"Chrome not available: {e}")
        print("Trying Edge...")
        edge_options = webdriver.EdgeOptions()
        edge_options.add_argument("--headless")
        edge_options.add_argument("--no-sandbox")
        edge_options.add_argument("--disable-dev-shm-usage")
        edge_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        driver = webdriver.Edge(options=edge_options)
        return driver


def get_total_pages(driver):
    """Get total number of pages from pagination."""
    try:
        # Find the pager element and extract page count
        pager = driver.find_element(By.ID, "ppager")
        pager_text = pager.text
        # Look for "Page X of Y" or "X of Y"
        import re
        match = re.search(r'of\s+(\d+)', pager_text)
        if match:
            return int(match.group(1))
    except:
        pass
    return 1


def click_next_page(driver):
    """Click the next page button in pagination."""
    try:
        # Find the next page link (not disabled)
        next_button = driver.find_element(
            By.CSS_SELECTOR, 
            "#ppager a.k-link.k-pager-nav[aria-label='Go to the next page']:not(.k-state-disabled)"
        )
        if next_button:
            # Scroll to the pagination element
            driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
            time.sleep(1)
            # Click it
            driver.execute_script("arguments[0].click();", next_button)
            return True
    except:
        pass
    return False


def extract_disease_outbreak_headlines(driver):
    """Extract all disease outbreak news headlines from the page."""
    print("\nExtracting headlines...")
    
    # Get page source and parse with BeautifulSoup
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    headlines = []
    
    # Find all <a> tags with class "sf-list-vertical__item"
    # Based on actual HTML structure from the page
    items = soup.find_all('a', class_='sf-list-vertical__item')
    
    print(f"Found {len(items)} Disease Outbreak News items")
    
    for item in items:
        # Verify this is a Disease Outbreak News item
        date_div = item.find('div', class_='sf-list-vertical__date')
        if not date_div or 'Disease Outbreak News' not in date_div.get_text():
            continue
        
        # Extract URL from the <a> tag itself
        url = item.get('href')
        if url:
            url = urljoin(BASE_URL, url)
        else:
            continue
        
        # Extract title from h4 with class "sf-list-vertical__title"
        title_h4 = item.find('h4', class_='sf-list-vertical__title')
        title = None
        date_text = None
        
        if title_h4:
            # Try to get full title from the hidden span
            full_title_span = title_h4.find('span', class_='full-title')
            if full_title_span:
                title = full_title_span.get_text(strip=True)
            else:
                # Fallback to trimmed title
                trimmed_span = title_h4.find('span', class_='trimmed')
                if trimmed_span:
                    title = trimmed_span.get_text(strip=True)
            
            # Extract date from the date span (format: "26 January 2026 | ")
            # Look for spans without class that contain date pattern
            for span in title_h4.find_all('span', recursive=False):
                text = span.get_text(strip=True)
                # Check if it looks like a date (has numbers and ends with |)
                if '|' in text and any(c.isdigit() for c in text):
                    date_text = text.replace('|', '').strip()
                    break
        
        # Parse date to standard format
        parsed_date = parse_date(date_text) if date_text else None
        
        if title and url:
            headline_data = {
                'title': title,
                'url': url,
                'date': parsed_date or date_text,
                'category': 'Disease Outbreak News',
                'scraped_at': datetime.now().isoformat()
            }
            headlines.append(headline_data)
            print(f"  ✓ {title[:60]}...")
    
    return headlines


def extract_year_from_date(date_str):
    """Extract year from date string."""
    if not date_str:
        return None
    
    import re
    # Try to find a 4-digit year
    match = re.search(r'\b(19|20)\d{2}\b', str(date_str))
    if match:
        return int(match.group(0))
    return None


def parse_date(date_str):
    """Try to parse date string to ISO format."""
    if not date_str:
        return None
    
    date_str = date_str.strip()
    
    # Common date formats
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
            dt = datetime.strptime(date_str, fmt)
            return dt.date().isoformat()
        except:
            continue
    
    return date_str  # Return as-is if can't parse


def load_existing_headlines_by_year(output_dir):
    """Load existing headlines from year JSON files."""
    existing_by_year = {}
    existing_urls = set()
    
    if not os.path.exists(output_dir):
        return existing_by_year, existing_urls
    
    for filename in os.listdir(output_dir):
        if filename.endswith('.json') and filename[0].isdigit():
            year = filename.replace('.json', '')
            filepath = os.path.join(output_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    headlines = json.load(f)
                    existing_by_year[year] = headlines
                    # Track URLs for duplicate detection
                    for h in headlines:
                        if 'url' in h:
                            existing_urls.add(h['url'])
            except:
                pass
    
    return existing_by_year, existing_urls


def save_headlines_by_year(headlines_by_year, output_dir):
    """Save headlines grouped by year to separate JSON files."""
    total_saved = 0
    
    # Sort with custom key to handle both int and str years, unknown stays at end
    def sort_key(item):
        year_str = item[0]
        if year_str == 'unknown':
            return (1, 0)  # unknown goes to end
        try:
            year_int = int(year_str)
            return (0, -year_int)  # numeric years sorted descending
        except:
            return (1, 0)
    
    for year, headlines in sorted(headlines_by_year.items(), key=sort_key):
        filepath = os.path.join(output_dir, f"{year}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(headlines, f, ensure_ascii=False, indent=2)
        print(f"  ✅ Saved {len(headlines)} headlines to {year}.json")
        total_saved += len(headlines)
    return total_saved


def main():
    parser = argparse.ArgumentParser(description="Scrape WHO Disease Outbreak News headlines")
    parser.add_argument(
        "--min-year",
        type=int,
        default=2000,
        help="Minimum year to extract (default: 2000)"
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum number of pages to scrape (default: all pages)"
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help="Only include articles from last N days (overrides min-year)"
    )
    args = parser.parse_args()
    
    print("="*60)
    print("WHO Disease Outbreak News Scraper")
    print("="*60)
    if args.max_age_days:
        print(f"Max age: {args.max_age_days} days")
        # Override min_year based on max_age_days
        from datetime import datetime, timedelta
        cutoff_date = datetime.now() - timedelta(days=args.max_age_days)
        args.min_year = cutoff_date.year
        print(f"Adjusted min year: {args.min_year}")
    else:
        print(f"Min year: {args.min_year}")
    if args.max_pages:
        print(f"Max pages: {args.max_pages}")
    
    driver = None
    try:
        # Setup driver
        print("\n🚀 Starting browser...")
        driver = setup_driver()
        
        # Load page
        print(f"📄 Loading {BASE_URL}...")
        driver.get(BASE_URL)
        
        # Wait for content to load
        print("⏳ Waiting for page to load...")
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(5)  # Additional wait for JavaScript
        
        # Get total pages
        total_pages = get_total_pages(driver)
        if args.max_pages:
            total_pages = min(total_pages, args.max_pages)
        print(f"📄 Found {total_pages} pages to scrape")
        
        # Load existing headlines to avoid duplicates
        print("\n📂 Loading existing headlines...")
        existing_by_year, existing_urls = load_existing_headlines_by_year(OUTPUT_DIR)
        if existing_urls:
            print(f"  Found {len(existing_urls)} existing headlines to skip")
        
        # Extract headlines from all pages, organized by year
        headlines_by_year = existing_by_year.copy()  # Start with existing
        current_page = 1
        should_stop = False
        new_count = 0
        skipped_count = 0
        
        while current_page <= total_pages and not should_stop:
            print(f"\n📖 Scraping page {current_page}/{total_pages}...")
            
            # Wait for content to be present
            time.sleep(2)
            
            # Extract headlines from current page
            page_headlines = extract_disease_outbreak_headlines(driver)
            
            page_new = 0
            page_skipped = 0
            
            # Group by year and check if we should stop
            for headline in page_headlines:
                url = headline.get('url')
                year = extract_year_from_date(headline.get('date'))
                
                # Check if year is below minimum - stop immediately since articles are sorted by date
                if year and year < args.min_year:
                    print(f"  ⏹️  Reached year {year} (below minimum {args.min_year}), stopping extraction.")
                    should_stop = True
                    break
                
                # Check for duplicate
                if url in existing_urls:
                    skipped_count += 1
                    page_skipped += 1
                    continue
                
                # Add to appropriate year group
                if year:
                    if year not in headlines_by_year:
                        headlines_by_year[year] = []
                    headlines_by_year[year].append(headline)
                    existing_urls.add(url)  # Track this URL
                    new_count += 1
                    page_new += 1
                else:
                    # No year detected, add to "unknown" category
                    if 'unknown' not in headlines_by_year:
                        headlines_by_year['unknown'] = []
                    headlines_by_year['unknown'].append(headline)
                    existing_urls.add(url)
                    new_count += 1
                    page_new += 1
            
            print(f"  Extracted {len(page_headlines)} headlines: {page_new} new, {page_skipped} skipped")
            
            # Stop paginating if we reached minimum year
            if should_stop:
                print(f"  Stopping pagination - reached articles older than {args.min_year}")
                break
            
            # Try to go to next page
            if current_page < total_pages:
                print(f"  ➡️  Moving to page {current_page + 1}...")
                if click_next_page(driver):
                    time.sleep(3)  # Wait for next page to load
                    current_page += 1
                else:
                    print("  ⚠️  Could not find next page button, stopping.")
                    break
            else:
                break
        
        if not headlines_by_year:
            print("\n⚠️  No headlines found. The page structure might have changed.")
            print("Saving page source for inspection...")
            with open(os.path.join(OUTPUT_DIR, "page_source.html"), "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            print(f"Page source saved to {OUTPUT_DIR}/page_source.html")
        else:
            # Save to files organized by year
            print("\n💾 Saving headlines by year...")
            total_saved = save_headlines_by_year(headlines_by_year, OUTPUT_DIR)
            
            # Print summary
            print("\n" + "="*60)
            print(f"📊 SUMMARY")
            print("="*60)
            print(f"Total headlines in files: {total_saved}")
            print(f"New headlines extracted: {new_count}")
            print(f"Already existing (skipped): {skipped_count}")
            
            # Sort years with custom key to handle both int and str
            def sort_key(year_val):
                if year_val == 'unknown':
                    return (1, 0)
                try:
                    year_int = int(year_val)
                    return (0, -year_int)
                except:
                    return (1, 0)
            
            sorted_years = sorted(headlines_by_year.keys(), key=sort_key, reverse=True)
            year_strs = [str(y) for y in sorted_years if y != 'unknown']
            print(f"Years covered: {', '.join(year_strs)}")
            for year in sorted_years:
                print(f"  {year}: {len(headlines_by_year[year])} headlines")
    
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        if driver:
            print("\n🛑 Closing browser...")
            driver.quit()


if __name__ == "__main__":
    main()

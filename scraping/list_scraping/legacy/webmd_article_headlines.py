import json
import time
import re
from urllib.parse import quote, urlparse, urlunparse
from multiprocessing import Pool, Manager, current_process
from playwright.sync_api import sync_playwright, TimeoutError

# =========================
# CONFIG
# =========================

INPUT_JSON = "../../storage/webmd/webmd_health_topics.json"
OUTPUT_JSON = "../../storage/webmd/webmd_articles_list.json"

BASE_SEARCH_URL = "https://www.webmd.com/search"
FILTER_TYPE = "Article"

MAX_PAGES_PER_KEYWORD = 2
REQUEST_DELAY = 0.1
NUM_PROCESSES = 8  # Adjust based on CPU

DEFAULT_TAGS = ["WebMD", "Article"]

# =========================
# CLEANING
# =========================

def canonicalize_url(url):
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def normalize_title(title):
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


# =========================
# LOAD KEYWORDS
# =========================

def load_keywords(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return sorted(set(item["title"].strip() for item in data if "title" in item))


# =========================
# SCRAPER WORKER (per process)
# =========================

def scrape_worker(keyword):
    print(f"[{current_process().name}] Scraping: {keyword}")
    results = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            # Block heavy resources
            page.route(
                "**/*",
                lambda route, request: route.abort()
                if request.resource_type in {"image", "font", "media"}
                else route.continue_()
            )

            for pg in range(1, MAX_PAGES_PER_KEYWORD + 1):
                search_url = (
                    f"{BASE_SEARCH_URL}?query={quote(keyword)}&filter={FILTER_TYPE}&pg={pg}"
                )
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector("div.search-results-item", timeout=20000)
                except TimeoutError:
                    print(f"  ⚠️ Timeout on page {pg}")
                    break

                items = page.query_selector_all("div.search-results-item")
                # -------------------------------
                # NEW: Check for empty page
                if not items:
                    print(f"  ⚠️ No results on page {pg}, stopping pagination for '{keyword}'")
                    break
                # -------------------------------
                for item in items:
                    ctype = item.query_selector("div.search-results-ctype")
                    if not ctype or ctype.inner_text().strip() != "Article":
                        continue

                    link = item.query_selector("a.search-results-title-link")
                    if not link:
                        continue

                    title = link.inner_text().strip()
                    url = link.get_attribute("href")
                    desc_el = item.query_selector("div.search-results-description")
                    description = desc_el.inner_text().strip() if desc_el else None

                    clean_url = canonicalize_url(url)
                    norm_title = normalize_title(title)

                    results.append({
                        "title": title,
                        "url": clean_url,
                        "description": description,
                        "tags": DEFAULT_TAGS,
                        "source_keywords": [keyword],
                        "norm_title": norm_title
                    })

                time.sleep(REQUEST_DELAY)

            context.close()
            browser.close()
    except Exception as e:
        print(f"[ERROR] Keyword '{keyword}' failed: {e}")

    return results


# =========================
# MAIN
# =========================

def main():
    keywords = load_keywords(INPUT_JSON)
    print(f"[INFO] Loaded {len(keywords)} keywords")

    manager = Manager()
    articles_by_url = {}
    articles_by_title = {}

    with Pool(processes=NUM_PROCESSES) as pool:
        all_results = pool.map(scrape_worker, keywords)

    # Flatten list of lists
    flat_results = [item for sublist in all_results for item in sublist]

    # Deduplicate
    for item in flat_results:
        clean_url = item["url"]
        norm_title = item["norm_title"]
        keyword = item["source_keywords"][0]

        if clean_url in articles_by_url:
            articles_by_url[clean_url]["source_keywords"].append(keyword)
            continue

        if norm_title in articles_by_title:
            existing = articles_by_title[norm_title]
            existing["source_keywords"].append(keyword)
            articles_by_url[existing["url"]] = existing
            continue

        record = {
            "title": item["title"],
            "url": clean_url,
            "description": item["description"],
            "tags": item["tags"],
            "source_keywords": item["source_keywords"]
        }

        articles_by_url[clean_url] = record
        articles_by_title[norm_title] = record

    # Finalize
    final_articles = []
    for article in articles_by_url.values():
        article["source_keywords"] = sorted(list(set(article["source_keywords"])))
        final_articles.append(article)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(final_articles, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Saved {len(final_articles)} unique articles")


if __name__ == "__main__":
    main()
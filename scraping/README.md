# Scraping Module - Medical Source Data Collection

This module provides a unified framework for scraping medical content from three authoritative sources:
- **Harvard Health Publishing** (blogs, health topics, decision guides, medical procedures)
- **WebMD** (health topics A-Z, article search)
- **WHO** (news headlines, disease outbreak news, feature stories, fact sheets)

The framework separates **list scraping** (collecting article URLs/metadata) from **detail scraping** (fetching full article content).

---

## Quick Start

### 1. Environment Setup
```bash
cd misinformation_research
pip install -r requirements.txt
# For Selenium: pip install selenium webdriver-manager
# For Playwright (WebMD article search): pip install playwright && playwright install chromium
```

### 2. List Scraping (Collect Article URLs)

```bash
# From misinformation_research/ directory
python -m scraping.list_scraping.scrape_all --list          # List available sources
python -m scraping.list_scraping.scrape_all --source harvard_blogs
python -m scraping.list_scraping.scrape_all --source webmd_healthtopics
python -m scraping.list_scraping.scrape_all --source who_news_headlines --max-age-days 365
python -m scraping.list_scraping.scrape_all --all           # Run all sources
```

**Output**: JSON files in `storage/<source>/` (e.g., `storage/harvardhealth/harvard_blogs_list.json`)

### 3. Detail Scraping (Fetch Full Articles)

```bash
python -m scraping.detail_scraping.scrape_all --list
python -m scraping.detail_scraping.scrape_all --source harvard_details
python -m scraping.detail_scraping.scrape_all --source who_news_details --recent-days 30
python -m scraping.detail_scraping.scrape_all --source webmd_articles_details --workers 8
python -m scraping.detail_scraping.scrape_all --source harvard_details --test-mode --test-count 3
```

**Output**: Per-article JSON in `storage/<source>/<category>/` + aggregate files

### 4. Preprocessing (Deduplication & Grouping)

```bash
python -m scraping.preprocessing.filter_dup_articles_urls --help
python -m scraping.preprocessing.group_yearwise_articles --help
```

---

## Directory Structure

```
scraping/
├── common.py                              # Shared utilities (HTTP, parsing, IO)
├── list_scraping/
│   ├── config.py                          # LIST_SOURCES: 13 source configs
│   ├── extractors.py                      # HTML extractors for list pages
│   ├── scrape_all.py                      # Unified list scraper CLI
│   └── legacy/                            # Old per-source scripts (archived)
├── detail_scraping/
│   ├── config.py                          # DETAIL_SOURCES: 10 source configs
│   ├── extractors.py                      # Article parsers (6 parser profiles)
│   ├── scrape_all.py                      # Unified detail scraper CLI
│   └── legacy/                            # Old per-source scripts (archived)
├── preprocessing/
│   ├── filter_dup_articles_urls.py        # Deduplicate article URLs
│   └── group_yearwise_articles.py         # Group articles by year
└── README.md                              # This file
```

---

## Configuration System

Both list and detail scraping use **config-driven design**. All source parameters are in:
- `list_scraping/config.py` → `LIST_SOURCES` dict
- `detail_scraping/config.py` → `DETAIL_SOURCES` dict

### List Source Config Schema
```python
{
    "method": "bs4",                    # fetch method: bs4 | selenium | auto | playwright
    "target_url": "https://...",        # page to scrape
    "pagination": {                     # how to walk pages
        "mode": "url_param",            # none | url_param | url_path | letters | search | selenium_click
        "param": "page",                # query param for url_param
        "start_page": 1,
        "max_pages": 150,
        "stop_on_empty_page": True,
    },
    "selectors": { ... },               # CSS selectors for extraction
    "url_filter": { ... },              # netloc / path_prefix / path_contains
    "record": {                         # default fields per record
        "category": "Blog",
        "tags": ["Harvard Health", "Blog"],
    },
    "output": {
        "dir": "../../../storage/...",
        "filename": "output.json",
        "merge_existing": True,
    },
    "request": { ... },                 # timeout, delay, user_agent
    "selenium": { ... },                # wait_seconds, headless, wait_selector
}
```

### Detail Source Config Schema
```python
{
    "parser": "harvard",                # extraction profile (see extractors.py PARSERS)
    "fetch": "engine",                  # "engine" (HTTP by scraper) | "internal" (parser fetches)
    "concurrency": "sequential",        # "sequential" | "threaded"
    "workers": 4,                       # thread count for threaded mode
    "input": {
        "mode": "file",                 # file | year_files | index
        "path": "../../../storage/...",
    },
    "filters": {
        "min_year": 2000,
        "recent_days": 30,
        "max_articles": None,
    },
    "base_tags": ["Harvard Health", "Blog"],
    "output": {
        "dir": "...",
        "per_article": True,
        "aggregate": "aggregate.json",
        "group_by_year": False,
        "skip_existing": True,
        "continue_on_error": True,
    },
    "request": { ... },
    "selenium": { ... },
}
```

---

## Sources Reference

### List Scraping Sources (13)
| Source | Description | Pagination | Method |
|--------|-------------|------------|--------|
| `harvard_blogs` | Harvard Health blog index | url_param (page=1..150) | bs4 |
| `harvard_decision_guides` | Decision guides list | none | bs4 |
| `harvard_healthtopics` | Health A-Z topics | none | bs4 |
| `harvard_medicalprocedures` | Medical procedures list | none | bs4 |
| `webmd_healthtopics` | WebMD A-Z health topics | letters (a-z) | bs4 |
| `webmd_articles` | WebMD article search | search (Playwright) | playwright |
| `who_news_headlines` | WHO news room headlines | url_path (page N) | bs4 |
| `who_disease_outbreak_headlines` | WHO disease outbreak news | selenium_click | selenium |
| `who_feature_stories_headlines` | WHO feature stories | selenium_click | selenium |

### Detail Scraping Sources (10)
| Source | Parser | Input Mode | Concurrency |
|--------|--------|------------|-------------|
| `harvard_details` | harvard | file | sequential |
| `webmd_articles_details` | webmd_topic | file | threaded (8) |
| `webmd_healthtopics_details` | webmd_topic | file | sequential |
| `who_news_details` | who_news | year_files | sequential |
| `who_disease_outbreak_details` | who_don | year_files | threaded (4) |
| `who_feature_stories_details` | who_feature_story | year_files | threaded (4) |
| `who_fact_sheets` | who_fact_sheet | index | sequential |

---

## Common CLI Options

### List Scraping
```bash
--source NAME              # Source to scrape (required unless --all)
--all                      # Run all sources
--list                     # List available sources
--method bs4|selenium|auto # Override fetch method
--start-page N             # Start page number
--end-page N               # End page number (inclusive)
--max-pages N              # Maximum pages to scrape
--min-year YYYY            # Stop when older than this year
--max-age-days N           # Only keep records newer than N days
--letters "a b c"          # Letters for A-Z pagination
--output-dir PATH          # Override output directory
--output-file NAME         # Override output filename
--request-delay FLOAT      # Delay between requests (seconds)
--request-timeout INT      # HTTP timeout (seconds)
--user-agent STRING        # Custom User-Agent
--headed                   # Run Selenium in headed mode
```

### Detail Scraping
```bash
--source NAME              # Source to scrape (required unless --all)
--all                      # Run all sources
--list                     # List available sources
--method bs4|selenium|auto # Override fetch method
--input PATH               # Override input JSON path
--output-dir PATH          # Override output directory
--aggregate NAME           # Override aggregate filename
--min-year YYYY            # Only process articles from this year
--recent-days N            # Only process articles from last N days
--max-articles N           # Maximum articles to process
--limit N                  # Limit input items (index mode)
--test-mode                # Only process first N records
--test-count N             # Records to process in test mode
--workers N                # Worker threads (threaded sources)
--request-delay FLOAT      # Delay between requests
--request-timeout INT      # HTTP timeout
--user-agent STRING        # Custom User-Agent
--no-skip-existing         # Re-scrape existing articles
--stop-on-error            # Stop immediately on any error
```

---

## Output Formats

### List Scraping Output
```json
[
  {
    "title": "Metformin is a wonder drug?",
    "url": "https://www.health.harvard.edu/blog/...",
    "category": "Blog",
    "scraped_at": "2026-01-15T10:30:00Z",
    "tags": ["Harvard Health Publishing", "Blog"]
  }
]
```

### Detail Scraping Output (per article)
```json
{
  "url": "https://www.health.harvard.edu/blog/...",
  "title": "Is metformin a wonder drug?",
  "slug": "is-metformin-a-wonder-drug",
  "published_date": "2021-09-29",
  "author": "Harvard Health Publishing",
  "medically_reviewed_by": "Dr. Name",
  "tags": ["Harvard Health Publishing", "Blog", "Diabetes"],
  "meta_description": "Metformin is a first-line treatment...",
  "scrape_timestamp_utc": "2026-01-15T10:30:00Z",
  "sections": [
    {
      "heading": "The bottom line",
      "content_blocks": [
        {"type": "paragraph", "text": "Metformin is a first-line treatment...", "associated_bullets": null},
        {"type": "bullets", "items": ["Affordable", "Well-tolerated", "Cardiovascular benefits"]}
      ]
    }
  ]
}
```

---

## Adding New Sources

### 1. Add List Source Config
Edit `list_scraping/config.py`:
```python
LIST_SOURCES["new_source"] = {
    "method": "bs4",
    "target_url": "https://example.com/list",
    "pagination": {"mode": "url_param", "param": "page", "max_pages": 50},
    "selectors": {"article_link": "a.link-class", "title": "h2"},
    "url_filter": {"netloc": "example.com"},
    "record": {"category": "News", "tags": ["Example", "News"]},
    "output": {"dir": "../../../storage/example", "filename": "list.json"},
}
```

### 2. Add Extractor
Edit `list_scraping/extractors.py`:
```python
def extract_new_source(html: str, cfg: Dict[str, Any]) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    # ... extraction logic
    return dedupe_records(records)

EXTRACTORS["new_source"] = extract_new_source
```

### 3. Add Detail Source Config
Edit `detail_scraping/config.py`:
```python
DETAIL_SOURCES["new_details"] = {
    "parser": "new_parser",  # or existing parser
    "fetch": "engine",
    "concurrency": "sequential",
    "input": {"mode": "file", "path": "../../../storage/example/list.json"},
    "output": {"dir": "../../../storage/example/articles", "per_article": True},
}
```

### 4. Add Parser (if needed)
Edit `detail_scraping/extractors.py`:
```python
def parse_new(item: dict, html: str, final_url: str, cfg: Dict[str, Any]) -> dict:
    # ... parsing logic
    return {...}

PARSERS["new_parser"] = parse_new
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: scraping` | Run from `misinformation_research/` directory, or `PYTHONPATH=.` |
| Selenium/Chrome not found | Install Chrome/Chromium, or use `--method bs4` |
| Playwright timeout | Increase timeout, check network, try `--headed` |
| Rate limiting / 429 errors | Increase `request.delay`, reduce `workers` |
| Empty results | Check CSS selectors in config, site may have changed |
| Unicode errors | Ensure `encoding="utf-8"` in file opens (already handled) |

---

## Reproducing the Full Pipeline

```bash
# 1. Scrape all list pages (URLs + metadata)
cd misinformation_research
python -m scraping.list_scraping.scrape_all --all

# 2. Scrape all article details (full content)
python -m scraping.detail_scraping.scrape_all --all

# 3. Preprocess for Qdrant ingestion
python -m scraping.preprocessing.filter_dup_articles_urls
python -m scraping.preprocessing.group_yearwise_articles

# 4. Ingest into Qdrant
python build_qdrant_incremental.py --source harvardhealth
python build_qdrant_incremental.py --source webmd
python build_qdrant_incremental.py --source who
```

---

## Dependencies

### Core (required)
- `requests` - HTTP client
- `beautifulsoup4` - HTML parsing
- `lxml` - Fast HTML parser

### Optional (for specific sources/methods)
- `selenium` + `webdriver-manager` - JavaScript rendering (WHO, some Harvard)
- `playwright` - WebMD article search (multiprocessing)

---

## Files Reference

| File | Purpose |
|------|---------|
| `common.py` | HTTP (requests/Selenium), date parsing, JSON IO, deduplication |
| `list_scraping/config.py` | 13 list source configurations |
| `list_scraping/extractors.py` | 8 extractor functions for list pages |
| `list_scraping/scrape_all.py` | Unified list scraper with 6 pagination modes |
| `detail_scraping/config.py` | 10 detail source configurations |
| `detail_scraping/extractors.py` | 6 parser profiles for article pages |
| `detail_scraping/scrape_all.py` | Unified detail scraper (sequential/threaded) |
| `preprocessing/filter_dup_articles_urls.py` | URL deduplication |
| `preprocessing/group_yearwise_articles.py` | Year-based grouping |

---

## License
Internal research code. See parent project for licensing.
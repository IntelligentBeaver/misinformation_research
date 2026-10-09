#!/usr/bin/env python3
"""
Configuration for all list (headline/index) scraping sources.

This file defines LIST_SOURCES - a dictionary mapping source names to their
scraping configuration. Each source is driven entirely by config, no code changes
needed to add new sources (just add extractor to extractors.py).

Config Schema:
    name            unique key used on CLI (--source <name>)
    method          default fetch method: bs4 | selenium | auto | playwright
    target_url      page to scrape (for single/url_param/url_path modes)
    pagination      how to walk pages:
                        mode: none | url_param | url_path | letters | search | selenium_click
    selectors       CSS selectors used by the extractor
    url_filter      which links to keep (netloc / path_prefix / path_contains)
    record          default category + tags written into each record
    output          where and how to write results
    request         timeout / delay / user-agent
    selenium        wait_seconds / headless / wait_selector

All relative paths are resolved against this file's directory.

Usage:
    from scraping.list_scraping.config import get_source_config, list_source_names
    cfg = get_source_config("harvard_blogs")
    names = list_source_names()

See scraping/README.md for full documentation.
"""

from __future__ import annotations

from typing import Any, Dict

from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Shared request / selenium defaults
# ---------------------------------------------------------------------------

DEFAULT_REQUEST: Dict[str, Any] = {
    "timeout": 30,
    "delay": 0.1,
    "user_agent": "Mozilla/5.0 (compatible; ResearchScraper/1.0)",
}

DEFAULT_SELENIUM: Dict[str, Any] = {
    "wait_seconds": 15,
    "headless": True,
    "wait_selector": "body",
}

HARVARD_BASE = "https://www.health.harvard.edu"
HARVARD_NETLOC = "www.health.harvard.edu"

# ---------------------------------------------------------------------------
# List sources
# ---------------------------------------------------------------------------

LIST_SOURCES: Dict[str, Dict[str, Any]] = {
    # ------------------------- Harvard Health Publishing -------------------------
    "harvard_blogs": {
        "method": "bs4",
        "target_url": f"{HARVARD_BASE}/blog",
        "pagination": {
            "mode": "url_param",
            "param": "page",
            "start_page": 1,
            "max_pages": 150,
            "stop_on_empty_page": True,
            "stop_on_existing_duplicate": True,
        },
        "selectors": {
            "article_list": 'div[data-cypress="article-list"]',
            "article_card": 'div[data-cypress="article-list"] > div',
            "article_link": 'a[data-cypress="article-link"]',
            "title": "h2",
        },
        "url_filter": {
            "netloc": HARVARD_NETLOC,
            "path_prefix": "/blog/",
        },
        "record": {
            "category": "Blog",
            "tags": ["Harvard Health Publishing", "Blog"],
        },
        "output": {
            "dir": "../../../storage/harvardhealth",
            "filename": "harvard_blogs_list.json",
            "merge_existing": True,
        },
        "request": {
            **DEFAULT_REQUEST,
            "user_agent": "Mozilla/5.0 (compatible; HarvardHealthBlogsScraper/1.0)",
        },
        "selenium": {
            **DEFAULT_SELENIUM,
            "wait_selector": 'div[data-cypress="article-list"]',
        },
    },

    "harvard_decision_guides": {
        "method": "bs4",
        "target_url": f"{HARVARD_BASE}/decision-guides",
        "pagination": {"mode": "none"},
        "selectors": {
            "primary": "div.content-link-list a.content-link",
            "fallbacks": [
                "div.content-link-list div.content-link-container a",
                "article div.mt-6 > ul > li > a",
                "main div > ul > li > a",
                "article div > ul > li > a",
            ],
        },
        "url_filter": {"netloc": HARVARD_NETLOC},
        "record": {
            "category": "Decision Guides",
            "tags": ["Harvard Health Publishing", "Decision Guides"],
        },
        "output": {
            "dir": "../../../storage/harvardhealth",
            "filename": "harvard_decision_guides_list.json",
            "merge_existing": False,
        },
        "request": {
            **DEFAULT_REQUEST,
            "user_agent": "Mozilla/5.0 (compatible; HarvardHealthTopicsScraper/1.0)",
        },
        "selenium": {
            **DEFAULT_SELENIUM,
            "wait_selector": "div.content-link-list a.content-link",
        },
    },

    "harvard_healthtopics": {
        "method": "bs4",
        "target_url": f"{HARVARD_BASE}/health-a-to-z",
        "pagination": {"mode": "none"},
        "selectors": {
            "fallbacks": [
                "main div > ul > li > a",
                "article div > ul > li > a",
                "div.entry-content ul li a",
                "div > ul > li > a",
            ],
        },
        "url_filter": {"netloc": HARVARD_NETLOC, "path_contains": "-a-to-z"},
        "record": {
            "category": "Health Topics",
            "tags": ["Harvard Health Publishing", "Health A to Z"],
        },
        "output": {
            "dir": "../../../storage/harvardhealth",
            "filename": "harvard_health_topics_list.json",
            "merge_existing": False,
        },
        "request": {
            **DEFAULT_REQUEST,
            "user_agent": "Mozilla/5.0 (compatible; HarvardHealthTopicsScraper/1.0)",
        },
        "selenium": {
            **DEFAULT_SELENIUM,
            "wait_selector": "div > ul > li > a",
        },
    },

    "harvard_medicalprocedures": {
        "method": "bs4",
        "target_url": f"{HARVARD_BASE}/diagnostic-tests-and-medical-procedures",
        "pagination": {"mode": "none"},
        "selectors": {
            "primary": "div.mt-6 > ul.list-none",
            "fallbacks": [
                "main div.mt-6 > ul > li > a",
                "article div.mt-6 > ul > li > a",
                "main div > ul > li > a",
                "article div > ul > li > a",
            ],
        },
        "url_filter": {"netloc": HARVARD_NETLOC},
        "record": {
            "category": "Medical Procedures",
            "tags": ["Harvard Health Publishing", "Diagnostic Tests and Medical Procedures"],
        },
        "output": {
            "dir": "../../../storage/harvardhealth",
            "filename": "harvard_medical_procedures_list.json",
            "merge_existing": False,
        },
        "request": {
            **DEFAULT_REQUEST,
            "user_agent": "Mozilla/5.0 (compatible; HarvardHealthTopicsScraper/1.0)",
        },
        "selenium": {
            **DEFAULT_SELENIUM,
            "wait_selector": "div > ul > li > a",
        },
    },

    # ------------------------- WebMD -------------------------
    "webmd_healthtopics": {
        "method": "bs4",
        "target_url": "https://www.webmd.com/a-to-z-guides/health-topics",
        "pagination": {
            "mode": "letters",
            "letters": [chr(c) for c in range(ord("a"), ord("z") + 1)],
        },
        "selectors": {
            "link": "section.content-section ul.link-list li a",
        },
        "url_filter": {},
        "record": {
            "tags": ["Health Topics", "WebMD"],
        },
        "output": {
            "dir": "../../storage/webmd",
            "filename": "webmd_health_topics.json",
            "merge_existing": False,
        },
        "request": {
            **DEFAULT_REQUEST,
            "delay": 1.0,
            "timeout": 15,
            "user_agent": "webmd-scraper/1.0 (+https://your-research.example)",
        },
        "selenium": dict(DEFAULT_SELENIUM),
    },

    "webmd_articles": {
        "method": "playwright",
        "pagination": {
            "mode": "search",
            "input_json": "../../storage/webmd/webmd_health_topics.json",
            "base_search_url": "https://www.webmd.com/search",
            "filter_type": "Article",
            "max_pages_per_keyword": 2,
            "num_processes": 8,
        },
        "selectors": {
            "item": "div.search-results-item",
            "ctype": "div.search-results-ctype",
            "link": "a.search-results-title-link",
            "description": "div.search-results-description",
        },
        "url_filter": {},
        "record": {
            "tags": ["WebMD", "Article"],
        },
        "output": {
            "dir": "../../storage/webmd",
            "filename": "webmd_articles_list.json",
            "merge_existing": False,
        },
        "request": {
            **DEFAULT_REQUEST,
            "delay": 0.1,
        },
        "selenium": dict(DEFAULT_SELENIUM),
    },

    # ------------------------- WHO -------------------------
    "who_news_headlines": {
        "method": "bs4",
        "target_url": "https://www.who.int/news-room/headlines",
        "pagination": {
            "mode": "url_path",
            "start_page": 1,
            "end_page": None,
            "stop_on_empty_page": True,
        },
        "selectors": {
            "container": "div.list-view.vertical-list.vertical-list--image",
            "anchor": "a[href^='/news/item/']",
        },
        "url_filter": {},
        "record": {},
        "filters": {
            "max_age_days": 7305,
        },
        "output": {
            "dir": "../../../storage/who/headlines",
            "group_by_year": True,
            "merge_existing": False,
        },
        "request": {
            **DEFAULT_REQUEST,
            "delay": 0.5,
            "timeout": 20,
            "user_agent": "Mozilla/5.0 (compatible; WHO-Headlines-Scraper/1.0)",
        },
        "selenium": dict(DEFAULT_SELENIUM),
    },

    "who_disease_outbreak_headlines": {
        "method": "selenium",
        "target_url": "https://www.who.int/emergencies/disease-outbreak-news",
        "pagination": {
            "mode": "selenium_click",
            "total_pages_selector": "#ppager",
            "next_button_selector": "#ppager a.k-link.k-pager-nav[aria-label='Go to the next page']:not(.k-state-disabled)",
            "min_year": 2000,
        },
        "selectors": {
            "item_class": "sf-list-vertical__item",
            "date_class": "sf-list-vertical__date",
            "title_class": "sf-list-vertical__title",
            "full_title_class": "full-title",
            "trimmed_title_class": "trimmed",
        },
        "url_filter": {},
        "record": {
            "category": "Disease Outbreak News",
        },
        "output": {
            "dir": "../../../storage/who/disease_outbreak_headlines",
            "group_by_year": True,
            "merge_existing": True,
        },
        "request": {
            **DEFAULT_REQUEST,
            "delay": 0,
        },
        "selenium": {
            **DEFAULT_SELENIUM,
            "wait_seconds": 20,
            "wait_selector": "a.sf-list-vertical__item",
        },
    },

    "who_feature_stories_headlines": {
        "method": "selenium",
        "target_url": "https://www.who.int/news-room/feature-stories",
        "pagination": {
            "mode": "selenium_click",
            "total_pages_selector": "#ppager",
            "next_button_selector": "#ppager a.k-link.k-pager-nav[aria-label='Go to the next page']:not(.k-state-disabled)",
            "min_year": 2000,
        },
        "selectors": {
            "item_class": "list-view--item",
            "link_class": "link-container",
            "title_class": "heading",
            "date_class": "date",
            "timestamp_class": "timestamp",
        },
        "url_filter": {},
        "record": {
            "category": "Feature Story",
        },
        "output": {
            "dir": "../../../storage/who/feature_stories_headlines",
            "group_by_year": True,
            "merge_existing": True,
        },
        "request": {
            **DEFAULT_REQUEST,
            "delay": 0,
        },
        "selenium": {
            **DEFAULT_SELENIUM,
            "wait_seconds": 20,
            "wait_selector": "div.list-view--item",
        },
    },
}


def get_source_config(name: str) -> Dict[str, Any]:
    """Return a deep-ish copy of a source config by name."""
    import copy

    if name not in LIST_SOURCES:
        raise KeyError(
            f"Unknown source '{name}'. Available: {', '.join(sorted(LIST_SOURCES))}"
        )
    return copy.deepcopy(LIST_SOURCES[name])


def list_source_names() -> list:
    return sorted(LIST_SOURCES)

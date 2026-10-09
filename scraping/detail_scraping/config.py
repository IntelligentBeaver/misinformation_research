#!/usr/bin/env python3
"""
Configuration for all detail (article) scraping sources.

This file defines DETAIL_SOURCES - a dictionary mapping source names to their
detail scraping configuration. Each source is driven entirely by config.

Config Schema:
    name        unique key used on the CLI (--source <name>)
    parser      extraction profile (see extractors.py PARSERS)
    fetch       "engine" (engine fetches HTML, parser receives it)
                "internal" (parser fetches the page itself)
    concurrency "sequential" | "threaded"
    workers     thread count for threaded mode
    input       where the URL list comes from:
                    mode: file | year_files | index
    filters     min_year / recent_days / max_articles / test_count
    output      where and how results are written:
                    dir, per_article, aggregate, group_by_year,
                    new_articles_dir, skip_existing, continue_on_error
    request     timeout / delay / user-agent

All relative paths are resolved against this file's directory.

Usage:
    from scraping.detail_scraping.config import get_source_config, list_source_names
    cfg = get_source_config("harvard_details")
    names = list_source_names()

See scraping/README.md for full documentation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

CONFIG_DIR = Path(__file__).resolve().parent

DEFAULT_REQUEST: Dict[str, Any] = {
    "timeout": 30,
    "delay": 0.1,
    "user_agent": "Mozilla/5.0 (compatible; ResearchScraper/1.0)",
}

# ---------------------------------------------------------------------------
# Detail sources
# ---------------------------------------------------------------------------

DETAIL_SOURCES: Dict[str, Dict[str, Any]] = {
    # ------------------------- Harvard Health Publishing -------------------------
    "harvard_details": {
        "parser": "harvard",
        "fetch": "engine",
        "concurrency": "sequential",
        "input": {
            "mode": "file",
            "path": "../../../storage/harvardhealth/harvard_medical_procedures_list.json",
        },
        "filters": {
            "min_year": None,
            "recent_days": None,
            "max_articles": None,
            "test_mode": False,
            "test_count": 1,
        },
        "base_tags": ["Harvard Health Publishing", "Blog"],
        "output": {
            "dir": "../../../storage/harvardhealth/medical_procedures",
            "per_article": True,
            "aggregate": "harvard_medical_procedures_articles.json",
            "skip_existing": True,
            "continue_on_error": True,
        },
        "request": {
            **DEFAULT_REQUEST,
            "user_agent": "Mozilla/5.0 (compatible; HarvardHealthBlogDetailsScraper/1.0)",
        },
        "selenium": {
            "wait_seconds": 15,
            "headless": True,
            "wait_selector": "body",
        },
    },

    # ------------------------- WebMD -------------------------
    "webmd_articles_details": {
        "parser": "webmd_topic",
        "fetch": "engine",
        "concurrency": "threaded",
        "workers": 8,
        "input": {
            "mode": "file",
            "path": "../../storage/webmd/webmd_articles_list_cleaned.json",
        },
        "filters": {
            "min_year": None,
            "recent_days": None,
            "max_articles": None,
        },
        "output": {
            "dir": "../../storage/webmd/articles",
            "per_article": True,
            "aggregate": None,
            "group_by_year": False,
            "skip_existing": False,
            "continue_on_error": True,
        },
        "request": {
            **DEFAULT_REQUEST,
            "delay": 0.1,
            "timeout": 20,
            "user_agent": "webmd-topic-scraper/1.0 (+https://verifact.scrape)",
        },
        "selenium": {
            "wait_seconds": 15,
            "headless": True,
            "wait_selector": "body",
        },
    },

    "webmd_healthtopics_details": {
        "parser": "webmd_topic",
        "fetch": "engine",
        "concurrency": "sequential",
        "input": {
            "mode": "file",
            "path": "../../storage/webmd/webmd_health_topics.json",
        },
        "filters": {
            "min_year": None,
            "recent_days": None,
            "max_articles": None,
        },
        "output": {
            "dir": "../../storage/webmd/healthtopics",
            "per_article": True,
            "aggregate": None,
            "skip_existing": False,
            "continue_on_error": True,
        },
        "request": {
            **DEFAULT_REQUEST,
            "delay": 0.1,
            "timeout": 20,
            "user_agent": "webmd-topic-scraper/1.0 (+https://verifact.scrape)",
        },
        "selenium": {
            "wait_seconds": 15,
            "headless": True,
            "wait_selector": "body",
        },
    },

    # ------------------------- WHO -------------------------
    "who_news_details": {
        "parser": "who_news",
        "fetch": "internal",
        "concurrency": "sequential",
        "input": {
            "mode": "year_files",
            "dir": "../../../storage/who/headlines",
        },
        "filters": {
            "min_year": 2006,
            "recent_days": None,
            "max_articles": None,
        },
        "output": {
            "dir": "../../../storage/who/news",
            "group_by_year": True,
            "new_articles_dir": "../../../storage/who/new_articles",
            "skip_existing": True,
            "continue_on_error": True,
            "save_every": 5,
        },
        "request": {
            **DEFAULT_REQUEST,
            "delay": 0.1,
            "timeout": 15,
            "user_agent": "Mozilla/5.0 (WHO-scraper/1.0)",
        },
        "selenium": {
            "wait_seconds": 15,
            "headless": True,
            "wait_selector": "body",
        },
    },

    "who_disease_outbreak_details": {
        "parser": "who_don",
        "fetch": "internal",
        "concurrency": "threaded",
        "workers": 4,
        "input": {
            "mode": "year_files",
            "dir": "../../../storage/who/disease_outbreak_headlines",
        },
        "filters": {
            "min_year": 2000,
            "recent_days": None,
            "max_articles": None,
        },
        "output": {
            "dir": "../../../storage/who/disease_outbreak_news",
            "group_by_year": True,
            "skip_existing": True,
            "continue_on_error": True,
            "save_every": 5,
        },
        "request": {
            **DEFAULT_REQUEST,
            "delay": 0.5,
            "timeout": 15,
            "user_agent": "Mozilla/5.0 (WHO-scraper/1.0)",
        },
        "selenium": {
            "wait_seconds": 15,
            "headless": True,
            "wait_selector": "body",
        },
    },

    "who_feature_stories_details": {
        "parser": "who_feature_story",
        "fetch": "internal",
        "concurrency": "threaded",
        "workers": 4,
        "input": {
            "mode": "year_files",
            "dir": "../../../storage/who/feature_stories_headlines",
        },
        "filters": {
            "min_year": 2000,
            "recent_days": None,
            "max_articles": None,
        },
        "output": {
            "dir": "../../../storage/who/feature_stories_news",
            "group_by_year": True,
            "skip_existing": True,
            "continue_on_error": True,
            "save_every": 5,
        },
        "request": {
            **DEFAULT_REQUEST,
            "delay": 0.5,
            "timeout": 15,
            "user_agent": "Mozilla/5.0 (WHO-scraper/1.0)",
        },
        "selenium": {
            "wait_seconds": 15,
            "headless": True,
            "wait_selector": "body",
        },
    },

    "who_fact_sheets": {
        "parser": "who_fact_sheet",
        "fetch": "internal",
        "concurrency": "sequential",
        "input": {
            "mode": "index",
            "index_url": "https://www.who.int/news-room/fact-sheets",
            "link_prefix": "/news-room/fact-sheets/detail/",
        },
        "filters": {
            "min_year": None,
            "recent_days": None,
            "max_articles": None,
            "limit": None,
        },
        "output": {
            "dir": "../../../storage/who/factsheets",
            "per_article": True,
            "skip_existing": False,
            "continue_on_error": True,
        },
        "request": {
            **DEFAULT_REQUEST,
            "delay": 0.1,
            "timeout": 15,
            "user_agent": "Mozilla/5.0 (compatible; WHO-FS-Extractor/1.0; +verifact.scrape.bot)",
        },
        "selenium": {
            "wait_seconds": 15,
            "headless": True,
            "wait_selector": "body",
        },
    },
}


def get_source_config(name: str) -> Dict[str, Any]:
    """Return a deep copy of a source config by name."""
    import copy

    if name not in DETAIL_SOURCES:
        raise KeyError(
            f"Unknown source '{name}'. Available: {', '.join(sorted(DETAIL_SOURCES))}"
        )
    return copy.deepcopy(DETAIL_SOURCES[name])


def list_source_names() -> list:
    return sorted(DETAIL_SOURCES)

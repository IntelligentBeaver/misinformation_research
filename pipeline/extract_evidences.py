#!/usr/bin/env python3
"""Extract evidence paragraphs from article JSON files.

Reads the scraped article JSON under storage/ and writes one evidence record
per content block (paragraph, with any associated bullets) to
storage/outputs/evidence/<source>_evidences.json.

Each source is sampled down to TARGET_EVIDENCE_COUNT records.

Usage:
    python pipeline/extract_evidences.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from evidence_extraction import (
    SourceFile,
    collect_source_files,
    extract_sections,
    load_json,
    make_article_key,
    make_evidence_id,
    normalize_space,
    normalize_to_doc_list,
)

PROJECT_ROOT = Path(__file__).resolve().parent

TARGET_EVIDENCE_COUNT = 1600
RANDOM_SEED: Optional[int] = None
BULLET_JOINER = "\n"

OUTPUT_DIR = Path("storage/outputs/evidence")
OUTPUT_FILES = {
    "harvardhealth": OUTPUT_DIR / "harvardhealth_evidences.json",
    "webmd": OUTPUT_DIR / "webmd_evidences.json",
    "who": OUTPUT_DIR / "who_evidences.json",
}

HARVARD_CONTENT_CATEGORY_BY_FILENAME = {
    "harvard_blogs_articles.json": "blog",
    "harvard_health_topics.json": "health_topic",
    "harvard_medical_procedures_articles.json": "medical_procedure",
}

SOURCE_CONFIGS = [
    {
        "name": "harvardhealth",
        "source_site": "harvardhealth",
        "input_paths": ["storage/harvardhealth/all"],
        "default_source_type": "health_portal",
        "content_category_by_filename": HARVARD_CONTENT_CATEGORY_BY_FILENAME,
    },
    {
        "name": "webmd",
        "source_site": "webmd",
        "input_paths": ["storage/webmd/articles", "storage/webmd/healthtopics"],
        "source_type_by_root": {
            "storage/webmd/articles": "article",
            "storage/webmd/healthtopics": "health_topic",
        },
    },
    {
        "name": "who",
        "source_site": "who",
        "input_paths": [
            "storage/who/news",
            "storage/who/feature_stories_news",
            "storage/who/disease_outbreak_news",
            "storage/who/factsheets",
        ],
        "source_type_by_root": {
            "storage/who/news": "news",
            "storage/who/feature_stories_news": "feature_story",
            "storage/who/disease_outbreak_news": "disease_outbreak",
            "storage/who/factsheets": "fact_sheet",
        },
    },
]

EXCLUDED_ARTICLE_FIELDS = {
    "sections",
    "content",
    "content_blocks",
    "claim",
    "claim_label",
    "id",
}


def build_evidences_for_article(
    article: Dict[str, Any],
    source_site: str,
    source_type: str,
    content_category: Optional[str],
    source_file: str,
) -> List[Dict[str, Any]]:
    base_payload = {k: v for k, v in article.items() if k not in EXCLUDED_ARTICLE_FIELDS}

    article_key = article.get("article_key") or make_article_key(article)

    base_payload.update(
        {
            "source_site": source_site,
            "source_type": source_type,
            "source_file": source_file,
            "article_key": article_key,
        }
    )

    if content_category:
        base_payload["content_category"] = content_category

    evidences: List[Dict[str, Any]] = []

    for section_index, (heading, blocks) in enumerate(extract_sections(article)):
        for block_index, block in enumerate(blocks):
            paragraph_text = normalize_space(str(block.get("text") or ""))

            bullets = block.get("associated_bullets")
            if bullets is None:
                bullets = block.get("bullets")
            if bullets is None:
                bullets = []
            if not isinstance(bullets, list):
                bullets = [bullets]

            bullet_texts = [normalize_space(str(item)) for item in bullets if normalize_space(str(item))]
            bullets_text = BULLET_JOINER.join(bullet_texts) if bullet_texts else ""

            if paragraph_text and bullets_text:
                exact_text = f"{paragraph_text}{BULLET_JOINER}{bullets_text}"
            else:
                exact_text = paragraph_text or bullets_text

            if not exact_text:
                continue

            evidence_id = make_evidence_id(source_site, article_key, section_index, block_index)

            evidence = {
                **base_payload,
                "evidence_id": evidence_id,
                "exact_evidence_text": exact_text,
                "section_heading": heading,
                "gold_evidences": [
                    {
                        "evidence_id": evidence_id,
                        "exact_evidence_text": exact_text,
                    }
                ],
            }

            evidences.append(evidence)

    return evidences


def sample_evidences(
    items: List[Dict[str, Any]],
    target_count: int,
    rng: random.Random,
) -> List[Dict[str, Any]]:
    rng.shuffle(items)
    if len(items) > target_count:
        return items[:target_count]
    return items


def run_for_source(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    source_files: List[SourceFile] = collect_source_files(config)
    all_evidences: List[Dict[str, Any]] = []
    total_articles = 0
    files_processed = 0

    for source_file in source_files:
        data = load_json(source_file.path)
        articles = normalize_to_doc_list(data)
        files_processed += 1
        total_articles += len(articles)

        for article in articles:
            all_evidences.extend(
                build_evidences_for_article(
                    article=article,
                    source_site=source_file.source_site,
                    source_type=source_file.source_type,
                    content_category=source_file.content_category,
                    source_file=source_file.path.name,
                )
            )

    rng = random.Random(RANDOM_SEED) if RANDOM_SEED is not None else random.Random()
    sampled = sample_evidences(all_evidences, TARGET_EVIDENCE_COUNT, rng)

    print(
        f"[{config['name']}] files={files_processed} articles={total_articles} "
        f"evidences={len(all_evidences)} sampled={len(sampled)}"
    )

    return sampled


def write_json(path: Path, records: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def main() -> None:
    for config in SOURCE_CONFIGS:
        records = run_for_source(config)
        output_path = OUTPUT_FILES.get(config["name"])
        if not output_path:
            raise ValueError(f"Missing OUTPUT_FILES entry for {config['name']}")
        write_json(output_path, records)

    print("Done.")


if __name__ == "__main__":
    main()

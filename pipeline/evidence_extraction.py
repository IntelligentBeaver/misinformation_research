#!/usr/bin/env python3
"""Shared helpers for extracting evidence passages from scraped article JSON.

Used by pipeline/extract_evidences.py (full evidence records) and
pipeline/extract_minimal_evidences.py (minimal records for claim-label generation).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent

ARTICLE_KEY_HEX_LEN = 12
EVIDENCE_ID_HEX_LEN = 12
SKIP_FILENAME_SUFFIXES = [".error.json"]


@dataclass
class SourceFile:
    path: Path
    source_site: str
    source_type: str
    content_category: Optional[str]


def sha1_hex(value: str) -> str:
    return sha1(value.encode("utf-8")).hexdigest()


def normalize_space(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.split()).strip()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_to_doc_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        if data.get("error"):
            return []
        if isinstance(data.get("items"), list):
            return [item for item in data["items"] if isinstance(item, dict)]
        if isinstance(data.get("records"), list):
            return [item for item in data["records"] if isinstance(item, dict)]
        if any(k in data for k in ("url", "title", "sections", "content")):
            return [data]
        return [value for value in data.values() if isinstance(value, dict)]

    return []


def iter_json_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    if root.is_dir():
        for path in root.rglob("*.json"):
            if any(path.name.endswith(suffix) for suffix in SKIP_FILENAME_SUFFIXES):
                continue
            yield path


def resolve_source_type(config: Dict[str, Any], file_path: Path) -> str:
    mapping = config.get("source_type_by_root")
    if mapping:
        resolved_roots = {
            (PROJECT_ROOT / key).resolve() if not Path(key).is_absolute() else Path(key).resolve(): value
            for key, value in mapping.items()
        }
        for root, source_type in sorted(
            resolved_roots.items(), key=lambda item: len(item[0].parts), reverse=True
        ):
            if root == file_path or root in file_path.parents:
                return source_type
    return config.get("default_source_type") or "unknown"


def resolve_content_category(config: Dict[str, Any], file_path: Path) -> Optional[str]:
    mapping = config.get("content_category_by_filename")
    if mapping:
        return mapping.get(file_path.name)
    return None


def collect_source_files(config: Dict[str, Any]) -> List[SourceFile]:
    source_files: List[SourceFile] = []
    for raw_path in config.get("input_paths", []):
        root = (PROJECT_ROOT / raw_path).resolve()
        for path in iter_json_files(root):
            source_files.append(
                SourceFile(
                    path=path,
                    source_site=config["source_site"],
                    source_type=resolve_source_type(config, path),
                    content_category=resolve_content_category(config, path),
                )
            )
    return source_files


def make_article_key(article: Dict[str, Any]) -> str:
    url = normalize_space(str(article.get("url") or ""))
    title = normalize_space(str(article.get("title") or ""))
    seed = url or title
    if not seed:
        seed = json.dumps(article, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha1_hex(seed)[:ARTICLE_KEY_HEX_LEN]


def make_evidence_id(
    source_site: str,
    article_key: str,
    section_index: int,
    block_index: int,
) -> str:
    raw = f"{source_site}|{article_key}|s{section_index}|b{block_index}"
    digest = sha1_hex(raw)[:EVIDENCE_ID_HEX_LEN]
    return f"ev_{digest}"


def normalize_blocks(content: Any) -> List[Dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, list):
        blocks: List[Dict[str, Any]] = []
        for item in content:
            if isinstance(item, dict):
                blocks.append(
                    {
                        "text": item.get("text"),
                        "associated_bullets": item.get("bullets") or item.get("associated_bullets"),
                    }
                )
            elif isinstance(item, str):
                blocks.append({"text": item, "associated_bullets": None})
        return blocks
    if isinstance(content, dict):
        return [
            {
                "text": content.get("text"),
                "associated_bullets": content.get("bullets") or content.get("associated_bullets"),
            }
        ]
    if isinstance(content, str):
        return [{"text": content, "associated_bullets": None}]
    return []


def extract_sections(article: Dict[str, Any]) -> List[Tuple[Optional[str], List[Dict[str, Any]]]]:
    sections = article.get("sections")
    if sections is None:
        sections = article.get("content")

    if sections is None:
        return []

    normalized: List[Tuple[Optional[str], List[Dict[str, Any]]]] = []

    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict):
                heading = section.get("heading")
                blocks = section.get("content_blocks")
                if blocks is None:
                    blocks = normalize_blocks(section.get("content"))
                if not isinstance(blocks, list):
                    blocks = normalize_blocks(blocks)
                normalized.append((heading, [b for b in blocks if isinstance(b, dict)]))
            elif isinstance(section, str):
                normalized.append((None, [{"text": section, "associated_bullets": None}]))
        return normalized

    if isinstance(sections, dict):
        heading = sections.get("heading")
        blocks = sections.get("content_blocks")
        if blocks is None:
            blocks = normalize_blocks(sections.get("content"))
        if not isinstance(blocks, list):
            blocks = normalize_blocks(blocks)
        normalized.append((heading, [b for b in blocks if isinstance(b, dict)]))

    return normalized

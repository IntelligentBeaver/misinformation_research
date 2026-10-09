#!/usr/bin/env python3
"""Incremental source -> Qdrant ingestion for WHO, WebMD, and Harvard Health.

This script consolidates the former build_{who,webmd,harvard}_qdrant_incremental.py
scripts into a single parameterized pipeline.

What this script does:
1. Reads JSON article sources for the selected source.
2. Flattens each document into passage-level records.
3. Computes two embeddings per passage:
   - Primary retrieval embedding
   - SapBERT embedding
4. Upserts into one Qdrant collection (no full rebuild).
5. Tracks per-document fingerprints in a local state file so reruns only process
   new/updated documents.

Incremental behavior:
- New doc: appended to Qdrant.
- Updated doc: old points for that doc are deleted (configurable), then re-upserted.
- Removed doc from input: old points are deleted (configurable).

Usage:
    python build_qdrant_incremental.py --source who
    python build_qdrant_incremental.py --source webmd
    python build_qdrant_incremental.py --source harvardhealth
    python build_qdrant_incremental.py --source who --force

Requirements:
    pip install qdrant-client sentence-transformers numpy tqdm python-dotenv
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointIdsList, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ============================================================================
# PER-SOURCE CONFIGURATION
# ============================================================================

SOURCE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "who": {
        "source_site": "who",
        "state_file": "storage/outputs/who/qdrant_ingest_state.json",
        # Directory inputs: each directory is scanned for *.json files.
        "inputs": [
            {"path": "storage/who/news", "source_type": "news"},
            {"path": "storage/who/feature_stories_news", "source_type": "feature_story"},
            {"path": "storage/who/disease_outbreak_news", "source_type": "disease_outbreak"},
            {"path": "storage/who/factsheets", "source_type": "fact_sheet"},
        ],
        # Article fields copied into each passage payload.
        "payload_fields": [
            "url",
            "title",
            "published_date",
            "author",
            "medically_reviewed_by",
            "topics",
            "location",
            "tags",
            "scrape_timestamp_utc",
            "first_seen_utc",
        ],
        # WHO stores references as list of {text, url} dicts or plain strings.
        "normalize_sources": True,
    },
    "webmd": {
        "source_site": "webmd",
        "state_file": "storage/outputs/webmd/qdrant_ingest_state.json",
        "inputs": [
            {"path": "storage/webmd/articles", "source_type": "article"},
            {"path": "storage/webmd/healthtopics", "source_type": "health_topic"},
        ],
        "payload_fields": [
            "url",
            "title",
            "published_date",
            "author",
            "medically_reviewed_by",
            "tags",
            "first_letter",
            "read_time",
            "sources",
            "scrape_timestamp_utc",
        ],
        "normalize_sources": False,
    },
    "harvardhealth": {
        "source_site": "harvardhealth",
        "state_file": "storage/outputs/harvardhealth/qdrant_ingest_state.json",
        # Harvard sources are aggregate JSON files (lists of articles).
        "files": [
            "storage/harvardhealth/all/harvard_blogs_articles.json",
            "storage/harvardhealth/all/harvard_health_topics.json",
            "storage/harvardhealth/all/harvard_medical_procedures_articles.json",
        ],
        "source_type_by_filename": {
            "harvard_blogs_articles.json": "blog",
            "harvard_health_topics.json": "health_topic",
            "harvard_medical_procedures_articles.json": "medical_procedure",
        },
        "payload_fields": [
            "url",
            "title",
            "published_date",
            "author",
            "medically_reviewed_by",
            "tags",
            "meta_description",
            "scrape_timestamp_utc",
        ],
        "normalize_sources": False,
    },
}

FILE_GLOB = "*.json"

# Qdrant connection. Set QDRANT_URL and QDRANT_API_KEY in the environment
# or in a local .env file (never commit real credentials).
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = "medical_facts_global"

# Models. Set PRIMARY_RETRIEVER_MODEL to a fine-tuned local folder after
# running retriever/train_triplet_retriever.py.
EMBEDDING_MODEL = os.getenv("PRIMARY_RETRIEVER_MODEL", "BAAI/bge-m3")
SAPBERT_MODEL = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"

# Processing controls
BATCH_SIZE = 32
UPSERT_BATCH_SIZE = 128
DELETE_BATCH_SIZE = 256
NORMALIZE_EMBEDDINGS = True

# Incremental controls
DELETE_OLD_PASSAGES_FOR_UPDATED_DOC = True
DELETE_REMOVED_DOCS = True
FORCE_REPROCESS_ALL_DOCS = False

# ============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha1_hex(value: str) -> str:
    return sha1(value.encode("utf-8")).hexdigest()


def normalize_space(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.split()).strip()


def dedupe_keep_order(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def canonical_hash(obj: Any) -> str:
    serialized = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha1_hex(serialized)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_to_doc_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        if data.get("error"):
            return []

        if any(
            k in data
            for k in ("url", "title", "sections", "content", "content_blocks", "published_date")
        ):
            return [data]

        if isinstance(data.get("items"), list):
            return [x for x in data["items"] if isinstance(x, dict)]

        if isinstance(data.get("records"), list):
            return [x for x in data["records"] if isinstance(x, dict)]

        docs_from_values = [v for v in data.values() if isinstance(v, dict)]
        return docs_from_values

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    return []


def make_doc_uid(source_site: str, source_type: str, article: Dict[str, Any]) -> str:
    url = normalize_space(str(article.get("url") or ""))
    title = normalize_space(str(article.get("title") or ""))
    key = f"{source_site}|{source_type}|{url or title}"
    return sha1_hex(key)


def normalize_references(refs: Any) -> List[str]:
    if refs is None:
        return []

    if not isinstance(refs, list):
        refs = [refs]

    out: List[str] = []
    for ref in refs:
        if isinstance(ref, str):
            text = normalize_space(ref)
            if text:
                out.append(text)
            continue

        if isinstance(ref, dict):
            txt = normalize_space(str(ref.get("text") or ""))
            url = normalize_space(str(ref.get("url") or ""))
            if txt and url:
                out.append(f"{txt} ({url})")
            elif txt:
                out.append(txt)
            elif url:
                out.append(url)

    return dedupe_keep_order(out)


def flatten_content_item(item: Any) -> str:
    if isinstance(item, str):
        return normalize_space(item)

    if not isinstance(item, dict):
        return ""

    paragraph_text = normalize_space(str(item.get("text") or ""))
    bullets = item.get("bullets")
    if bullets is None:
        bullets = item.get("associated_bullets")

    if bullets is None:
        bullets = []
    if not isinstance(bullets, list):
        bullets = [str(bullets)]

    bullets = [normalize_space(str(bullet)) for bullet in bullets if normalize_space(str(bullet))]

    if bullets:
        bullets_text = "\n".join(bullets)
        return (paragraph_text + "\n" + bullets_text).strip() if paragraph_text else bullets_text

    return paragraph_text


def build_passages(
    article: Dict[str, Any],
    source_site: str,
    source_type: str,
    source_file: str,
    payload_fields: List[str],
    normalize_sources: bool,
) -> Tuple[str, List[Dict[str, Any]], List[str]]:
    doc_uid = make_doc_uid(source_site, source_type, article)

    base_payload = {
        "source_site": source_site,
        "source_type": source_type,
        "source_file": source_file,
        "doc_id": doc_uid,
    }
    for field in payload_fields:
        if field == "sources" and normalize_sources:
            base_payload["sources"] = normalize_references(article.get("references"))
        else:
            base_payload[field] = article.get(field)

    sections = article.get("sections")
    if sections is None:
        sections = article.get("content")
    if sections is None:
        sections = []

    passages: List[Dict[str, Any]] = []
    point_ids: List[str] = []
    seen_texts = set()

    if isinstance(sections, list):
        for section_index, section in enumerate(sections):
            if isinstance(section, str):
                section_heading = article.get("title")
                content_items = [section]
            elif isinstance(section, dict):
                section_heading = section.get("heading") or article.get("title")
                raw_content = section.get("content_blocks")
                if raw_content is None:
                    raw_content = section.get("content")
                if isinstance(raw_content, list):
                    content_items = raw_content
                elif raw_content is None:
                    content_items = []
                else:
                    content_items = [raw_content]
            else:
                continue

            for block_index, item in enumerate(content_items):
                full_text = flatten_content_item(item)
                if not full_text:
                    continue

                # Deduplicate repeated content fragments inside one document.
                if full_text in seen_texts:
                    continue
                seen_texts.add(full_text)

                passage_id = f"{doc_uid}_s{section_index}_b{block_index}"
                point_id = sha1_hex(
                    f"{source_site}|{passage_id}|{article.get('url') or article.get('title') or ''}"
                )

                payload = {
                    **base_payload,
                    "passage_id": passage_id,
                    "section_heading": section_heading,
                    "block_index": block_index,
                    "text": full_text,
                }

                passages.append({"point_id": point_id, "payload": payload, "text": full_text})
                point_ids.append(point_id)

    return doc_uid, passages, dedupe_keep_order(point_ids)


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1e-10
    return vectors / norms


def load_state(path: Path, source_site: str) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "source_site": source_site, "docs": {}}

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        return {"version": 1, "source_site": source_site, "docs": {}}

    data.setdefault("docs", {})
    return data


def save_state(
    path: Path,
    docs_state: Dict[str, Any],
    source_site: str,
    input_sources: List[Dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": 1,
        "source_site": source_site,
        "updated_at_utc": utc_now_iso(),
        "input_sources": input_sources,
        "docs": docs_state,
    }

    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def batched(items: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def ensure_collection(client: QdrantClient, primary_dim: int, sapbert_dim: int) -> None:
    if client.collection_exists(COLLECTION_NAME):
        print(f"[Qdrant] Collection exists: {COLLECTION_NAME}")
        return

    print(f"[Qdrant] Creating collection: {COLLECTION_NAME}")
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            "primary": VectorParams(size=primary_dim, distance=Distance.COSINE),
            "sapbert": VectorParams(size=sapbert_dim, distance=Distance.COSINE),
        },
    )


def delete_points(client: QdrantClient, point_ids: List[str]) -> int:
    if not point_ids:
        return 0

    unique_ids = dedupe_keep_order(point_ids)
    print(f"[Qdrant] Deleting {len(unique_ids)} stale points...")

    deleted = 0
    for chunk in tqdm(list(batched(unique_ids, DELETE_BATCH_SIZE)), desc="Delete batches"):
        client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=PointIdsList(points=chunk),
            wait=True,
        )
        deleted += len(chunk)

    return deleted


def embed_texts(model_name: str, texts: List[str], batch_size: int, normalize: bool) -> np.ndarray:
    print(f"[Embed] Loading model: {model_name}")
    model = SentenceTransformer(model_name)

    print(f"[Embed] Encoding {len(texts)} passages...")
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
    )

    if normalize:
        vectors = l2_normalize(vectors)
        print("[Embed] L2 normalization applied")

    return vectors


def upsert_points(
    client: QdrantClient,
    passages: List[Dict[str, Any]],
    primary_vectors: np.ndarray,
    sapbert_vectors: np.ndarray,
) -> int:
    total = len(passages)
    if total == 0:
        return 0

    all_points: List[PointStruct] = []
    for i, passage in enumerate(passages):
        all_points.append(
            PointStruct(
                id=passage["point_id"],
                vector={
                    "primary": primary_vectors[i].astype(float).tolist(),
                    "sapbert": sapbert_vectors[i].astype(float).tolist(),
                },
                payload=passage["payload"],
            )
        )

    print(f"[Qdrant] Upserting {total} points...")
    upserted = 0
    for chunk in tqdm(list(batched(all_points, UPSERT_BATCH_SIZE)), desc="Upsert batches"):
        client.upsert(collection_name=COLLECTION_NAME, points=chunk, wait=True)
        upserted += len(chunk)

    return upserted


def iter_source_docs(source_path: Path, file_glob: str) -> Iterable[Tuple[Dict[str, Any], str]]:
    if source_path.is_file():
        data = load_json(source_path)
        docs = normalize_to_doc_list(data)
        for doc in docs:
            yield doc, source_path.name
        return

    if source_path.is_dir():
        json_files = sorted(source_path.glob(file_glob))
        for file_path in tqdm(json_files, desc=f"Read {source_path.name}"):
            try:
                data = load_json(file_path)
            except Exception as exc:
                print(f"  [Warn] Failed to parse {file_path.name}: {exc}")
                continue

            docs = normalize_to_doc_list(data)
            for doc in docs:
                yield doc, file_path.name
        return

    raise FileNotFoundError(f"Input source not found: {source_path}")


def resolve_input_entries(
    config: Dict[str, Any],
    project_root: Path,
) -> List[Dict[str, Any]]:
    """Resolve a source config into a list of {path, source_type} entries."""
    entries: List[Dict[str, Any]] = []

    for src in config.get("inputs") or []:
        rel_path = src.get("path")
        if not rel_path:
            continue
        entries.append(
            {
                "path": (project_root / rel_path).resolve(),
                "source_type": src.get("source_type") or "unknown",
            }
        )

    source_type_by_filename = config.get("source_type_by_filename") or {}
    for rel_path in config.get("files") or []:
        path = (project_root / rel_path).resolve()
        entries.append(
            {
                "path": path,
                "source_type": source_type_by_filename.get(path.name, "unknown"),
            }
        )

    return entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Incremental source -> Qdrant ingestion (WHO, WebMD, Harvard Health)",
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=sorted(SOURCE_CONFIGS),
        help="Source to ingest.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess all documents even if their fingerprint is unchanged.",
    )
    args = parser.parse_args()

    force_reprocess = FORCE_REPROCESS_ALL_DOCS or args.force
    config = SOURCE_CONFIGS[args.source]
    source_site = config["source_site"]

    project_root = Path(__file__).resolve().parent
    state_path = (project_root / config["state_file"]).resolve()

    print("=" * 72)
    print(f"{source_site} -> Qdrant Incremental Ingestion")
    print("=" * 72)
    print(f"[Config] Qdrant URL: {QDRANT_URL}")
    print(f"[Config] Collection: {COLLECTION_NAME}")
    print(f"[Config] State file: {state_path}")
    if force_reprocess:
        print("[Config] Force reprocess: ON")

    source_entries = resolve_input_entries(config, project_root)
    if not source_entries:
        raise RuntimeError(f"No valid input sources configured for '{args.source}'.")

    source_paths: List[Tuple[Path, str]] = []
    for entry in source_entries:
        abs_path: Path = entry["path"]
        source_type: str = entry["source_type"]
        if not abs_path.exists():
            print(f"  [Warn] Missing source, skipping: {abs_path}")
            continue
        source_paths.append((abs_path, source_type))
        print(f"  - {abs_path} ({source_type})")

    if not source_paths:
        raise RuntimeError("No valid input sources found.")

    state = load_state(state_path, source_site)
    previous_docs: Dict[str, Any] = state.get("docs", {})

    current_docs: Dict[str, Any] = {}
    passages_to_upsert: List[Dict[str, Any]] = []
    stale_ids_to_delete: List[str] = []

    docs_seen = 0
    docs_skipped = 0
    docs_new = 0
    docs_changed = 0

    print("[Step 1/5] Scanning and diffing source documents...")
    for source_path, source_type in source_paths:
        for doc, source_file in iter_source_docs(source_path, FILE_GLOB):
            docs_seen += 1
            if not isinstance(doc, dict) or not (doc.get("url") or doc.get("title")):
                continue
            if doc.get("error"):
                continue

            doc_uid = make_doc_uid(source_site, source_type, doc)
            fingerprint = canonical_hash(doc)
            previous = previous_docs.get(doc_uid)

            is_unchanged = (
                not force_reprocess
                and previous is not None
                and previous.get("fingerprint") == fingerprint
            )

            if is_unchanged:
                current_docs[doc_uid] = previous
                docs_skipped += 1
                continue

            _, passages, point_ids = build_passages(
                article=doc,
                source_site=source_site,
                source_type=source_type,
                source_file=source_file,
                payload_fields=config["payload_fields"],
                normalize_sources=config.get("normalize_sources", False),
            )

            if previous is None:
                docs_new += 1
            else:
                docs_changed += 1
                if DELETE_OLD_PASSAGES_FOR_UPDATED_DOC:
                    stale_ids_to_delete.extend(previous.get("point_ids") or [])

            current_docs[doc_uid] = {
                "fingerprint": fingerprint,
                "point_ids": point_ids,
                "source_type": source_type,
                "source_file": source_file,
                "updated_at_utc": utc_now_iso(),
            }
            passages_to_upsert.extend(passages)

    previous_doc_ids = set(previous_docs.keys())
    current_doc_ids = set(current_docs.keys())
    removed_doc_ids = sorted(previous_doc_ids - current_doc_ids)

    if removed_doc_ids and DELETE_REMOVED_DOCS:
        for doc_uid in removed_doc_ids:
            stale_ids_to_delete.extend(previous_docs.get(doc_uid, {}).get("point_ids") or [])

    print("[Step 2/5] Change summary")
    print(f"  Docs seen: {docs_seen}")
    print(f"  New docs: {docs_new}")
    print(f"  Changed docs: {docs_changed}")
    print(f"  Unchanged docs skipped: {docs_skipped}")
    print(f"  Removed docs: {len(removed_doc_ids)}")
    print(f"  Passages to upsert: {len(passages_to_upsert)}")

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

    print("[Step 3/5] Applying deletions for stale points...")
    deleted_count = 0
    if stale_ids_to_delete:
        deleted_count = delete_points(client, stale_ids_to_delete)
    print(f"  Deleted points: {deleted_count}")

    print("[Step 4/5] Embedding + upsert...")
    upserted_count = 0
    if passages_to_upsert:
        texts = [p["text"] for p in passages_to_upsert]

        primary_vectors = embed_texts(
            model_name=EMBEDDING_MODEL,
            texts=texts,
            batch_size=BATCH_SIZE,
            normalize=NORMALIZE_EMBEDDINGS,
        )
        sapbert_vectors = embed_texts(
            model_name=SAPBERT_MODEL,
            texts=texts,
            batch_size=BATCH_SIZE,
            normalize=NORMALIZE_EMBEDDINGS,
        )

        ensure_collection(
            client=client,
            primary_dim=int(primary_vectors.shape[1]),
            sapbert_dim=int(sapbert_vectors.shape[1]),
        )

        upserted_count = upsert_points(
            client=client,
            passages=passages_to_upsert,
            primary_vectors=primary_vectors,
            sapbert_vectors=sapbert_vectors,
        )
    else:
        print("  No new/updated passages to upsert.")

    print("[Step 5/5] Saving incremental state...")
    save_state(state_path, current_docs, source_site, source_entries)

    print("\n" + "=" * 72)
    print("Ingestion complete")
    print("=" * 72)
    print(f"Points upserted: {upserted_count}")
    print(f"Points deleted: {deleted_count}")
    print(f"Tracked documents: {len(current_docs)}")
    print(f"State written to: {state_path}")


if __name__ == "__main__":
    main()

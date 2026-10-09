#!/usr/bin/env python3
"""Extract minimal evidence records for claim-label generation.

Reads the full evidence records produced by pipeline/extract_evidences.py
(storage/outputs/evidence/<source>_evidences.json) and writes a minimal
version containing only the fields needed for claim-label generation:
evidence_id, exact_evidence_text, and empty claim fields.

Each source is sampled down to TARGET_EVIDENCE_COUNT records.

Usage:
    python pipeline/extract_minimal_evidences.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from evidence_extraction import load_json, normalize_to_doc_list

PROJECT_ROOT = Path(__file__).resolve().parent

TARGET_EVIDENCE_COUNT = 1600
RANDOM_SEED: Optional[int] = None

OUTPUT_DIR = PROJECT_ROOT / "storage" / "outputs" / "evidence"
INPUT_FILES = {
    "harvardhealth": OUTPUT_DIR / "harvardhealth_evidences.json",
    "webmd": OUTPUT_DIR / "webmd_evidences.json",
    "who": OUTPUT_DIR / "who_evidences.json",
}
OUTPUT_FILES = {
    "harvardhealth": OUTPUT_DIR / "harvardhealth_minimal_evidences_min.json",
    "webmd": OUTPUT_DIR / "webmd_minimal_evidences_min.json",
    "who": OUTPUT_DIR / "who_minimal_evidences_min.json",
}
SOURCE_NAMES = ("harvardhealth", "webmd", "who")


def extract_minimal_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "evidence_id": record.get("evidence_id", ""),
        "exact_evidence_text": record.get("exact_evidence_text", ""),
        "claim_id": "",
        "claim_label": "",
        "claim": "",
        "source_file": record.get("source_file", ""),
    }


def sample_records(
    items: List[Dict[str, Any]],
    target_count: int,
    rng: random.Random,
) -> List[Dict[str, Any]]:
    rng.shuffle(items)
    return items[:target_count] if len(items) > target_count else items


def run_for_source(name: str) -> List[Dict[str, Any]]:
    input_path = INPUT_FILES[name]
    data = load_json(input_path)
    source_records = normalize_to_doc_list(data)
    all_records = [extract_minimal_fields(record) for record in source_records]

    rng = random.Random(RANDOM_SEED) if RANDOM_SEED is not None else random.Random()
    sampled = sample_records(all_records, TARGET_EVIDENCE_COUNT, rng)

    print(f"[{name}] input={input_path.name} records={len(all_records)} sampled={len(sampled)}")
    return sampled


def write_json(path: Path, records: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def main() -> None:
    for name in SOURCE_NAMES:
        records = run_for_source(name)
        write_json(OUTPUT_FILES[name], records)

    print("Done.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Merge claim-evidence pairs into the master claim-evidence dataset.

Joins the merged claim output (storage/outputs/labels/<source>_merged_output.json)
with the full evidence records (storage/outputs/evidence/<source>_evidences.json),
producing one record per claim with all evidence metadata attached.

Source-specific fields that are not needed downstream are removed
(e.g. WebMD's first_letter/read_time, Harvard's slug/meta_description).

Usage:
    python pipeline/merge_c_e_to_master.py --source who
    python pipeline/merge_c_e_to_master.py --source webmd
    python pipeline/merge_c_e_to_master.py --source harvardhealth
"""

import argparse
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Set

PROJECT_ROOT = Path(__file__).resolve().parent

SOURCE_FILES = {
    "who": {
        "evidence": "storage/outputs/evidence/who_evidences.json",
        "claims": "storage/outputs/labels/who_merged_output.json",
        "output": "storage/outputs/dataset/who_final_dataset.json",
        "fields_to_remove": {"topics", "images"},
    },
    "harvardhealth": {
        "evidence": "storage/outputs/evidence/harvardhealth_evidences.json",
        "claims": "storage/outputs/labels/harvard_merged_output.json",
        "output": "storage/outputs/dataset/harvardhealth_final_dataset.json",
        "fields_to_remove": {"slug", "meta_description", "canonical_url", "content_category"},
    },
    "webmd": {
        "evidence": "storage/outputs/evidence/webmd_evidences.json",
        "claims": "storage/outputs/labels/webmd_merged_output.json",
        "output": "storage/outputs/dataset/webmd_final_dataset.json",
        "fields_to_remove": {
            "first_letter",
            "read_time",
            "pdfs",
            "images",
            "related_links",
            "meta_description",
            "canonical_url",
        },
    },
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def merge(
    evidence_path: Path,
    claims_path: Path,
    fields_to_remove: Set[str],
) -> List[Dict[str, Any]]:
    parent_data = load_json(evidence_path)
    claims_data = load_json(claims_path)

    # Ensure parent_data is a list
    if isinstance(parent_data, dict):
        parent_data = [parent_data]

    # Group claims by evidence_id
    claims_by_evidence_id: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in claims_data:
        claims_by_evidence_id[item["evidence_id"]].append(item)

    merged_output: List[Dict[str, Any]] = []

    for parent_item in parent_data:
        evidence_id = parent_item.get("evidence_id")
        matching_claims = claims_by_evidence_id.get(evidence_id, [])

        # If there are no matching claims, keep the parent item as-is
        if not matching_claims:
            merged_output.append(parent_item)
            continue

        # Create one output item per claim
        for claim_item in matching_claims:
            if "claim_label" not in claim_item:
                print("Missing claim_label in:", claim_item)
                continue

            merged_item = deepcopy(parent_item)

            # Remove source-specific fields that are not needed downstream
            for field in fields_to_remove:
                merged_item.pop(field, None)

            merged_item["claim_id"] = claim_item["claim_id"]
            merged_item["claim_label"] = claim_item["claim_label"]
            merged_item["claim"] = claim_item["claim"]

            merged_output.append(merged_item)

    return merged_output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge claim-evidence pairs into the master claim-evidence dataset.",
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=sorted(SOURCE_FILES),
        help="Source to merge.",
    )
    args = parser.parse_args()

    config = SOURCE_FILES[args.source]
    evidence_path = PROJECT_ROOT / config["evidence"]
    claims_path = PROJECT_ROOT / config["claims"]
    output_path = PROJECT_ROOT / config["output"]

    merged_output = merge(evidence_path, claims_path, config["fields_to_remove"])
    save_json(merged_output, output_path)

    print(f"Saved {len(merged_output)} records to {output_path}")


if __name__ == "__main__":
    main()

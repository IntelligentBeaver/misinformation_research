#!/usr/bin/env python3
"""Merge generated claims with their source evidence text.

Joins the LLM-generated claim labels
(storage/outputs/labels/<source>_evidences_labels.json) with the minimal
evidence records (storage/outputs/evidence/<source>_minimal_evidences_min.json),
attaching the exact evidence text to every claim.

Usage:
    python pipeline/merge_claim_to_evidence.py --source who
    python pipeline/merge_claim_to_evidence.py --source webmd
    python pipeline/merge_claim_to_evidence.py --source harvardhealth
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent

SOURCE_FILES = {
    "who": {
        "claims": "storage/outputs/labels/who_evidences_labels.json",
        "evidence": "storage/outputs/evidence/who_minimal_evidences_min.json",
        "output": "storage/outputs/labels/who_merged_output.json",
    },
    "harvardhealth": {
        "claims": "storage/outputs/labels/harvardhealth_evidences_labels.json",
        "evidence": "storage/outputs/evidence/harvardhealth_minimal_evidences_min.json",
        "output": "storage/outputs/labels/harvard_merged_output.json",
    },
    "webmd": {
        "claims": "storage/outputs/labels/webmd_evidences_labels.json",
        "evidence": "storage/outputs/evidence/webmd_minimal_evidences_min.json",
        "output": "storage/outputs/labels/webmd_merged_output.json",
    },
}

STRICT_MATCH = True  # if True, raises error if evidence_id missing in evidence file


def load_json(path: Path) -> List[Dict]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: List[Dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def build_evidence_map(evidence_list: List[Dict]) -> Dict[str, str]:
    """Maps evidence_id -> exact_evidence_text."""
    evidence_map: Dict[str, str] = {}

    for item in evidence_list:
        eid = item.get("evidence_id")
        text = item.get("exact_evidence_text")

        if eid is None:
            continue

        evidence_map[eid] = text

    return evidence_map


def merge_claims_with_evidence(
    claims: List[Dict],
    evidence_map: Dict[str, str],
) -> List[Dict]:
    merged_output = []
    missing_ids = set()

    for claim_obj in claims:
        eid = claim_obj.get("evidence_id")

        if eid in evidence_map:
            merged_obj = {
                **claim_obj,
                "exact_evidence_text": evidence_map[eid],
            }
            merged_output.append(merged_obj)
        else:
            missing_ids.add(eid)

            if STRICT_MATCH:
                raise ValueError(f"Missing evidence_id in evidence file: {eid}")
            # keep claim but without evidence text
            merged_output.append({**claim_obj, "exact_evidence_text": None})

    if missing_ids and not STRICT_MATCH:
        print(f"[WARN] Missing evidence IDs: {len(missing_ids)}")

    return merged_output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge generated claims with their source evidence text.",
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=sorted(SOURCE_FILES),
        help="Source whose claims and evidence should be merged.",
    )
    args = parser.parse_args()

    paths = SOURCE_FILES[args.source]
    claims_path = PROJECT_ROOT / paths["claims"]
    evidence_path = PROJECT_ROOT / paths["evidence"]
    output_path = PROJECT_ROOT / paths["output"]

    claims = load_json(claims_path)
    evidence = load_json(evidence_path)

    evidence_map = build_evidence_map(evidence)
    merged = merge_claims_with_evidence(claims, evidence_map)
    save_json(merged, output_path)

    print("Merge completed.")
    print(f"Total claims processed: {len(claims)}")
    print(f"Total merged records: {len(merged)}")
    print(f"Output saved to: {output_path}")


if __name__ == "__main__":
    main()

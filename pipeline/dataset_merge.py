#!/usr/bin/env python3
"""Merge the per-source final datasets into one combined dataset.

Reads the final claim-evidence datasets produced by pipeline/merge_c_e_to_master.py
and writes the combined dataset to storage/outputs/dataset/dataset.json.

Usage:
    python pipeline/dataset_merge.py
"""

import json
from pathlib import Path
from typing import Any, List

PROJECT_ROOT = Path(__file__).resolve().parent

INPUT_FILES = [
    "storage/outputs/dataset/harvardhealth_final_dataset.json",
    "storage/outputs/dataset/webmd_final_dataset.json",
    "storage/outputs/dataset/who_final_dataset.json",
]
OUTPUT_FILE = "storage/outputs/dataset/dataset.json"


def main() -> None:
    merged: List[Any] = []

    for rel_path in INPUT_FILES:
        file_path = PROJECT_ROOT / rel_path
        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            merged.extend(data if isinstance(data, list) else [data])

    output_path = PROJECT_ROOT / OUTPUT_FILE
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(merged)} records to {output_path}")


if __name__ == "__main__":
    main()

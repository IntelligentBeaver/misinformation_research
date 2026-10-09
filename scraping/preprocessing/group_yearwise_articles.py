#!/usr/bin/env python3
"""
Collect JSON article files from one or more directories and produce year-wise JSON files.

Behavior:
- Recursively finds all .json files under the input directories.
- For each file:
  - If the file contains a JSON object (dict), treat it as one article.
  - If the file contains a JSON array (list), treat each element as an article.
  - For each article (must be a dict), **only** look at the top-level key "published_date".
    - Extract the first 4-digit year found in that string and use it as the classification year.
    - If no year found or key missing, classify as "unknown".
- Outputs files named <year>.json (e.g. 2025.json) in output_dir/by_year (default).
- Overwrites any existing year files.

Usage:
    python classify_by_year.py --inputs storage/webmd/healthtopics storage/webmd/articles \
        --output-dir storage/webmd/yearly_output

Requirements:
    Python 3.7+
"""
from __future__ import annotations
import argparse
import json
import os
import re
from typing import Dict, List, Iterable, Tuple


def find_json_files(directories: Iterable[str]) -> List[str]:
    paths = []
    for d in directories:
        d = os.path.expanduser(d)
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for fn in files:
                if fn.lower().endswith(".json"):
                    paths.append(os.path.join(root, fn))
    return paths


def load_json_file(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: failed to load JSON {path!r}: {e}")
        return None


def extract_year_from_published_date(pub: object) -> str:
    """
    Given the value of the top-level 'published_date' key, return a year string like '2025'.
    Only inspects this value (the user requested to 'just search the key published_date').
    If no 4-digit year found, return 'unknown'.
    """
    if not pub:
        return "unknown"
    if not isinstance(pub, str):
        # If it's not a string, we won't try to inspect other fields — respect user's request
        return "unknown"
    m = re.search(r"(\d{4})", pub)
    return m.group(1) if m else "unknown"


def collect_articles_from_file(path: str) -> List[Dict]:
    """
    Return a list of article dicts found in the JSON file.
    If file contains a top-level dict -> single article.
    If it contains a list -> each element that is a dict is treated as an article.
    Non-dict items are ignored with a warning.
    """
    data = load_json_file(path)
    if data is None:
        return []
    articles = []
    if isinstance(data, dict):
        articles.append(data)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, dict):
                articles.append(item)
            else:
                # skip non-dict entries silently (or print a short warning)
                print(f"Note: skipping non-dict element #{i} in {path}")
    else:
        print(f"Note: JSON in {path} is neither object nor list; skipping.")
    return articles


def classify_articles_by_year(paths: Iterable[str]) -> Dict[str, List[Dict]]:
    """
    Walk all given json file paths, extract articles and group them by year (or 'unknown').
    """
    year_map: Dict[str, List[Dict]] = {}
    for p in paths:
        articles = collect_articles_from_file(p)
        for art in articles:
            if not isinstance(art, dict):
                continue
            pub = art.get("published_date", None)
            year = extract_year_from_published_date(pub)
            year_map.setdefault(year, []).append(art)
    return year_map


def write_year_files(year_map: Dict[str, List[Dict]], out_dir: str, pretty: bool = True) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for year, items in sorted(year_map.items()):
        out_path = os.path.join(out_dir, f"{year}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            if pretty:
                json.dump(items, f, ensure_ascii=False, indent=2)
            else:
                json.dump(items, f, ensure_ascii=False)
        print(f"Wrote {len(items)} items -> {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Classify JSON articles into year-wise files using top-level 'published_date'.")
    parser.add_argument("--inputs", "-i", nargs="+", required=True, help="One or more directories to scan recursively for .json files")
    parser.add_argument("--output-dir", "-o", default="by_year_output", help="Directory to write year files (default: by_year_output)")
    parser.add_argument("--year-subdir", default="by_year", help="Subdirectory name inside output-dir for year JSON files (default: by_year)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output (indentation)")
    args = parser.parse_args()

    json_paths = find_json_files(args.inputs)
    if not json_paths:
        print("No JSON files found in the provided input directories.")
        return

    print(f"Found {len(json_paths)} JSON files. Scanning for articles...")
    year_map = classify_articles_by_year(json_paths)

    final_dir = os.path.join(args.output_dir, args.year_subdir)
    write_year_files(year_map, final_dir, pretty=args.pretty)
    print("Done.")


if __name__ == "__main__":
    main()
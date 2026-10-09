import json
from urllib.parse import urlparse, urlunparse

# =========================
# CONFIG
# =========================

SOURCE_JSON = "../../../storage/webmd/webmd_articles_list.json"          # JSON you want to clean
REFERENCE_JSON = "../../../storage/webmd/webmd_health_topics.json"       # JSON with URLs to exclude
OUTPUT_JSON = "../../../storage/webmd/webmd_articles_list_cleaned.json"

# =========================
# URL NORMALIZATION
# =========================

def canonicalize_url(url):
    if not url:
        return None
    parsed = urlparse(url)
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        "", "", ""
    ))

# =========================
# LOAD JSON
# =========================

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# =========================
# MAIN CLEANING LOGIC
# =========================

def main():
    source_data = load_json(SOURCE_JSON)
    reference_data = load_json(REFERENCE_JSON)

    # Build set of canonical URLs to exclude
    reference_urls = {
        canonicalize_url(item.get("url"))
        for item in reference_data
        if item.get("url")
    }

    cleaned = []
    seen_urls = set()

    for item in source_data:
        url = item.get("url")
        clean_url = canonicalize_url(url)

        if not clean_url:
            continue

        # Skip if found in reference file
        if clean_url in reference_urls:
            continue

        # Skip internal duplicates
        if clean_url in seen_urls:
            continue

        seen_urls.add(clean_url)
        cleaned.append(item)

    # =========================
    # SAVE OUTPUT
    # =========================

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)

    print("✅ Cleaning complete")
    print(f"Original records : {len(source_data)}")
    print(f"Removed duplicates: {len(source_data) - len(cleaned)}")
    print(f"Final records    : {len(cleaned)}")
    print(f"Saved to         : {OUTPUT_JSON}")

# =========================

if __name__ == "__main__":
    main()

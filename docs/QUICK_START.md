# Quick Start Guide

This guide covers the complete pipeline for medical misinformation research:
scraping → labeling → retrieval training → Qdrant indexing.

---

## Prerequisites

```bash
cd misinformation_research
pip install -r requirements.txt
# For GPU training: pip install torch --index-url https://download.pytorch.org/whl/cu124
# For Selenium: pip install selenium webdriver-manager
# For Playwright: pip install playwright && playwright install chromium
```

Configure Qdrant connection (create `.env` in `misinformation_research/`):
```bash
QDRANT_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=your-api-key
QDRANT_COLLECTION=medical_facts_global
```

---

## Pipeline Overview

```
┌─────────────────┐    ┌─────────────────┐    ┌──────────────────┐    ┌──────────────┐
│  SCRAPING       │    │  LABELING       │    │  TRAINING        │    │  QDRANT      │
│                 │    │                 │    │                  │    │              │
│ 1. List scrape  │───▶│ 3. LLM label    │───▶│ 5. Triplet loss  │───▶│ 7. Ingest    │
│ 2. Detail scrape│    │  (Colab T4)     │    │  fine-tune       │    │  fine-tuned  │
└─────────────────┘    └─────────────────┘    └──────────────────┘    └──────────────┘
        │                       │                      │                   │
        ▼                       ▼                      ▼                   ▼
storage/*/list.json    retriever/data/labeled/  storage/models/    medical_facts_global
storage/*/articles/    claims_llm_labeled.json    fine_tuned_        collection
                                                              medical_retriever/
```

---

## Step-by-Step Commands

### 1. Scrape All Medical Sources

```bash
# From misinformation_research/ directory

# List scraping (collect article URLs + metadata)
python -m scraping.list_scraping.scrape_all --all

# Detail scraping (fetch full article content)
python -m scraping.detail_scraping.scrape_all --all
```

**Sources**: Harvard Health (4), WebMD (2), WHO (3) = 9 list sources, 7 detail sources

### 2. Label Claims with LLM (Colab)

1. Open `retriever/notebooks/colab_gemma_claim_labeler.ipynb` in Google Colab
2. Set runtime: **GPU T4**
3. Upload `retriever/data/raw/claims_sample.json`
4. Run all cells → downloads `claims_llm_labeled.json`
5. Save to `retriever/data/labeled/claims_llm_labeled.json`

### 3. Fine-tune Retriever Model

```bash
python -m retriever.train_triplet_retriever
```

**Output**: `storage/models/fine_tuned_medical_retriever/` (~1.3 GB)

### 4. Ingest into Qdrant

```bash
python build_qdrant_incremental.py --source harvardhealth
python build_qdrant_incremental.py --source webmd
python build_qdrant_incremental.py --source who
```

### 5. Query & Evaluate

Open `retriever/notebooks/qdrant_query.ipynb`:
```python
FINE_TUNE_MODEL_PATH = "storage/models/fine_tuned_medical_retriever"
```

---

## Alternative: Run Individual Sources

### List Scraping
```bash
python -m scraping.list_scraping.scrape_all --list          # Show sources
python -m scraping.list_scraping.scrape_all --source harvard_blogs
python -m scraping.list_scraping.scrape_all --source webmd_healthtopics
python -m scraping.list_scraping.scrape_all --source who_news_headlines --max-age-days 365
```

### Detail Scraping
```bash
python -m scraping.detail_scraping.scrape_all --list
python -m scraping.detail_scraping.scrape_all --source harvard_details
python -m scraping.detail_scraping.scrape_all --source who_news_details --recent-days 30
python -m scraping.detail_scraping.scrape_all --source webmd_articles_details --workers 8
python -m scraping.detail_scraping.scrape_all --source harvard_details --test-mode --test-count 3
```

### Retriever Training (Custom Config)
```python
from retriever.config import TrainingConfig
from retriever.train_triplet_retriever import main

main(TrainingConfig(epochs=4, batch_size=32, learning_rate=1e-5))
```

---

## Key Files & Directories

```
misinformation_research/
├── retriever/
│   ├── config.py                    # All configuration (dataclasses)
│   ├── retrieval.py                 # Shared MedicalEvidenceRetriever
│   ├── train_triplet_retriever.py   # Triplet loss training
│   ├── data/
│   │   ├── raw/claims_sample.json          # 200 input claims
│   │   ├── labeled/claims_llm_labeled.json # LLM-labeled (input to training)
│   │   └── training/retrieval_triplets.jsonl
│   ├── notebooks/
│   │   ├── qdrant_query.ipynb              # Query evaluation
│   │   └── colab_gemma_claim_labeler.ipynb # LLM labeling
│   └── README.md
├── scraping/
│   ├── list_scraping/scrape_all.py   # Unified list scraper (9 sources)
│   ├── detail_scraping/scrape_all.py # Unified detail scraper (7 sources)
│   ├── config.py (list + detail)     # Source configurations
│   └── README.md
├── build_qdrant_incremental.py       # Qdrant ingestion (3 sources)
├── requirements.txt
└── docs/
    ├── QUICK_START.md                # This file
    ├── report.md
    └── prompt.md
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: scraping` | Run from `misinformation_research/` directory |
| `QDRANT_URL not set` | Create `.env` file or export env vars |
| Selenium/Chrome not found | Install Chrome, or use `--method bs4` |
| Playwright timeout | Increase timeout, try `--headed` |
| CUDA OOM (training) | Reduce `batch_size` in `TrainingConfig` |
| Empty scrape results | Site CSS changed; update selectors in `config.py` |
| `No triplets found` | Ensure `claims_llm_labeled.json` has both pos/neg evidence |

---

## Next Steps

- **Full docs**: `retriever/README.md`, `scraping/README.md`
- **Detailed config**: `retriever/config.py`, `scraping/*/config.py`
- **Evaluation**: `retriever/notebooks/qdrant_query.ipynb`
- **Benchmarking**: `benchmark/` folder

---

## License
Internal research code. See parent project for licensing.
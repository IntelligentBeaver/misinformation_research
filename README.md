# Medical Misinformation Research Pipeline

A complete end-to-end pipeline for **medical evidence retrieval** and **misinformation detection**, built on authoritative health sources (Harvard Health, WebMD, WHO).

## Pipeline Overview

| Stage | Module | Key Actions | Output |
|-------|--------|-------------|--------|
| **1. Scraping** | `scraping/` | List scrape (9 sources) → Detail scrape (7 sources) | `storage/*/list.json`, `storage/*/articles/` |
| **2. Labeling** | `retriever/notebooks/` | LLM label claims on Colab T4 (Gemma-2-9b) | `retriever/data/labeled/claims_llm_labeled.json` |
| **3. Fine-Tuning** | `retriever/` | Triplet loss fine-tune BAAI/bge-m3 | `storage/models/fine_tuned_medical_retriever/` |
| **4. Retrieval** | `retriever/` | Qdrant index + hybrid search (dense + cross-encoder) | `medical_facts_global` collection |

## Quick Start

```bash
# 1. Clone and install
cd misinformation_research
pip install -r requirements.txt
# GPU: pip install torch --index-url https://download.pytorch.org/whl/cu124

# 2. Configure Qdrant (create .env)
QDRANT_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=your-api-key
QDRANT_COLLECTION=medical_facts_global

# 3. Scrape all sources
python -m scraping.list_scraping.scrape_all --all
python -m scraping.detail_scraping.scrape_all --all

# 4. Label claims (Colab T4)
# Open retriever/notebooks/colab_gemma_claim_labeler.ipynb

# 5. Fine-tune retriever
python -m retriever.train_triplet_retriever

# 6. Ingest to Qdrant
python build_qdrant_incremental.py --source harvardhealth
python build_qdrant_incremental.py --source webmd
python build_qdrant_incremental.py --source who

# 7. Query & evaluate
# Open retriever/notebooks/qdrant_query.ipynb
```

## Project Structure

```
misinformation_research/
├── .gitignore                    # Excludes data, models, secrets, __pycache__
├── requirements.txt              # Pinned dependencies (Python 3.13)
├── build_qdrant_incremental.py   # Streaming Qdrant ingestion for 3 sources
├── README.md                     # This file
├── docs/
│   ├── QUICK_START.md            # Complete step-by-step guide
│   ├── claim_generation_prompt.md
│   ├── myth_facts_generation_prompt.md
│   └── dataset_count.md
├── scraping/                     # Web scraping framework
│   ├── common.py                 # Shared HTTP, parsing, I/O utilities
│   ├── list_scraping/            # URL discovery (9 sources)
│   │   ├── config.py             # LIST_SOURCES: 13 configs
│   │   ├── extractors.py         # 8 HTML extractors
│   │   ├── scrape_all.py         # Unified CLI (6 pagination modes)
│   │   └── legacy/               # Archived per-source scripts
│   ├── detail_scraping/          # Full article extraction (7 sources)
│   │   ├── config.py             # DETAIL_SOURCES: 10 configs
│   │   ├── extractors.py         # 6 parser profiles
│   │   ├── scrape_all.py         # Unified CLI (sequential/threaded)
│   │   └── legacy/               # Archived per-source scripts
│   ├── preprocessing/            # Deduplication & grouping
│   │   ├── filter_dup_articles_urls.py
│   │   └── group_yearwise_articles.py
│   └── README.md                 # Detailed scraping docs
├── retriever/                    # Evidence retrieval & fine-tuning
│   ├── config.py                 # Centralized config (frozen dataclasses)
│   ├── retrieval.py              # MedicalEvidenceRetriever class
│   ├── train_triplet_retriever.py# Triplet loss training entry point
│   ├── data/
│   │   ├── raw/claims_sample.json           # 200 unlabeled claims
│   │   ├── labeled/claims_llm_labeled.json  # LLM-labeled (training input)
│   │   └── training/retrieval_triplets.jsonl
│   ├── notebooks/
│   │   ├── qdrant_query.ipynb               # Query & evaluation
│   │   └── colab_gemma_claim_labeler.ipynb  # LLM labeling (Colab T4)
│   └── README.md                 # Detailed retriever docs
├── pipeline/                     # Evidence extraction & merging
│   ├── extract_evidences.py      # Core NLP extraction
│   ├── evidence_extraction.py    # Extraction logic
│   ├── extract_minimal_evidences.py # Lightweight variant
│   ├── dataset_merge.py          # Multi-source dataset merge
│   ├── merge_claim_to_evidence.py # Claim-evidence alignment
│   └── merge_c_e_to_master.py    # Master dataset production
├── benchmark/                    # Evaluation & benchmarking
│   ├── benchmark_dataset/        # Gold-standard test queries
│   └── harvard_search_articles.py
├── notebooks/                    # Colab notebooks for index building
│   ├── build_harvard_qdrant_incremental_colab_2.ipynb
│   ├── build_webmd_qdrant_incremental_colab_2.ipynb
│   └── build_who_qdrant_incremental_colab_2.ipynb
├── storage/                      # Data artifacts (gitignored)
│   ├── harvardhealth/            # Scraped Harvard articles
│   ├── webmd/                    # Scraped WebMD articles
│   ├── who/                      # Scraped WHO articles
│   ├── models/                   # Fine-tuned model weights
│   └── datasets/                 # Intermediate datasets
└── classifier/                   # Reserved for claim classification
```

## Data Sources

| Source | List Sources | Detail Sources | Coverage |
|--------|--------------|----------------|----------|
| **Harvard Health** | 4 (blogs, decision guides, health topics, procedures) | 1 | General health, medical procedures |
| **WebMD** | 2 (health topics A-Z, article search) | 2 | Conditions, treatments, drugs |
| **WHO** | 3 (news, disease outbreaks, features) | 4 | Global health, outbreaks, fact sheets |

**Total**: 9 list sources, 7 detail sources

## Key Features

### Scraping
- **Config-driven**: Add new sources by editing `config.py` only
- **6 pagination modes**: url_param, url_path, letters, search, selenium_click, none
- **3 fetch methods**: requests (bs4), Selenium, Playwright
- **Resumable**: Skip existing, continue on error, test modes

### Retrieval
- **Dual-vector Qdrant**: Primary (bge-m3/fine-tuned) + SapBERT (medical)
- **Hybrid scoring**: Dense vector + cross-encoder reranking
- **Configurable weights**: `qdrant_weight` + `cross_encoder_weight`

### Fine-Tuning
- **Triplet loss**: Anchor (claim), Positive (supporting evidence), Negative (contradicting)
- **LLM labeling**: Gemma-2-9b on Colab T4 for evidence annotation
- **Reproducible**: Pinned deps, seeded RNG, versioned labeled data

## Configuration

All settings via **frozen dataclasses** in `retriever/config.py` and `scraping/*/config.py`.

Override via:
1. **Environment variables** (`.env` file):
   ```bash
   QDRANT_URL=... QDRANT_API_KEY=... python -m retriever.train_triplet_retriever
   ```
2. **Python config objects**:
   ```python
   from retriever.config import TrainingConfig
   from retriever.train_triplet_retriever import main
   main(TrainingConfig(epochs=4, batch_size=32, learning_rate=1e-5))
   ```

## Notebooks

| Notebook | Purpose | Runtime |
|----------|---------|---------|
| `retriever/notebooks/colab_gemma_claim_labeler.ipynb` | LLM evidence labeling | Colab T4 GPU |
| `retriever/notebooks/qdrant_query.ipynb` | Query, evaluate, analyze | Local/Colab |
| `notebooks/build_*_qdrant_incremental_colab_2.ipynb` | Incremental index builds | Colab |

## Requirements

- Python 3.13+
- `pip install -r requirements.txt`
- Optional GPU: `pip install torch --index-url https://download.pytorch.org/whl/cu124`
- Selenium: Chrome/Chromium installed
- Playwright: `playwright install chromium`

## License

Internal research code. See parent project for licensing.
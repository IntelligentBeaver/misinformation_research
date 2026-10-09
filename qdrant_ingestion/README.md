# Qdrant Ingestion (Colab)

Colab-ready notebooks for **incremental upsert** of scraped medical articles into Qdrant Cloud.

These notebooks are the **final pipeline step** — they embed articles using the fine-tuned retriever and SapBERT, then upsert to a dual-vector Qdrant collection (`medical_facts_global_1`).

## Notebooks

| Notebook | Source | Input Paths | Vectors |
|----------|--------|-------------|---------|
| `build_harvard_qdrant_incremental_colab_2.ipynb` | Harvard Health | `storage/harvardhealth/all/*.json` | primary (fine-tuned bge-m3) + sapbert |
| `build_webmd_qdrant_incremental_colab_2.ipynb` | WebMD | `storage/webmd/articles/`, `storage/webmd/healthtopics/` | primary + sapbert |
| `build_who_qdrant_incremental_colab_2.ipynb` | WHO | `storage/who/{news,feature_stories,disease_outbreak,factsheets}/` | primary + sapbert |

## Prerequisites

1. **Qdrant Cloud cluster** (URL + API key)
2. **Fine-tuned model** at `storage/models/fine_tuned_medical_retriever/`
3. **Scraped data** in `storage/` (run scraping pipeline first)
4. **Google Drive** mounted at `/content/drive/MyDrive/MisInformation_Research/`

## Quick Start (per notebook)

```bash
# 1. Open notebook in Google Colab
# 2. Set runtime: GPU T4 (for embedding speed)
# 3. Mount Drive (run cell 2)
# 4. Configure Qdrant (cell 3) - replace placeholders:
os.environ['QDRANT_URL'] = 'https://<your-cluster>.cloud.qdrant.io'
os.environ['QDRANT_API_KEY'] = '<your-api-key>'
# 5. Run all cells
```

## Configuration

All settings via environment variables (cell 3 in each notebook):

| Variable | Description | Default |
|----------|-------------|---------|
| `QDRANT_URL` | Qdrant Cloud endpoint | **Required** |
| `QDRANT_API_KEY` | API key for authentication | **Required** |
| `PROJECT_ROOT` | GDrive project root | `/content/drive/MyDrive/MisInformation_Research/data_scraping` |
| `COLLECTION_NAME` | Qdrant collection name | `medical_facts_global_1` |
| `FINE_TUNE_MODEL_PATH` | Path to fine-tuned bge-m3 | `/content/drive/.../fine_tuned_medical_retriever` |
| `BATCH_SIZE` | Embedding batch size | `16` (WebMD: `8` for OOM) |
| `UPSERT_BATCH_SIZE` | Qdrant upsert batch | `64` |
| `DELETE_BATCH_SIZE` | Qdrant delete batch | `128` |
| `EMBED_WRITE_CHUNK_SIZE` | Passages per embed+upsert loop | `512` |
| `MAX_PASSAGES_PER_DOC` | Cap passages per article | `40` (0 = no cap) |

## Incremental Logic

Each notebook maintains a **state file** (`storage/outputs/<source>/qdrant_ingest_state_colab_global_1.json`) tracking:
- `doc_hash`: SHA1 of article content
- `passage_ids`: Qdrant point IDs for that document
- `updated_at`: Last upsert timestamp

On re-run:
1. **New docs** → embed + upsert
2. **Changed docs** → delete old passages → embed + upsert new
3. **Removed docs** → delete passages (if `DELETE_REMOVED_DOCS=True`)
4. **Unchanged docs** → skipped

## Dual-Vector Schema

```python
VectorParams(
    name="primary",      # fine-tuned BAAI/bge-m3 (1024 dim)
    size=1024,
    distance=Distance.COSINE
)
VectorParams(
    name="sapbert",      # cambridgeltl/SapBERT (768 dim)
    size=768,
    distance=Distance.COSINE
)
```

## Expected Runtime

| Source | Articles | Est. Time (T4 GPU) |
|--------|----------|-------------------|
| Harvard | ~2,000 | 15-25 min |
| WebMD | ~50,000 | 2-4 hours |
| WHO | ~5,000 | 30-60 min |

## Common Issues

| Issue | Fix |
|-------|-----|
| `QDRANT_URL not set` | Fill in cell 3 placeholders |
| `Fine-tuned model path not found` | Run `retriever/train_triplet_retriever.py` first |
| CUDA OOM | Reduce `BATCH_SIZE` (WebMD uses 8) |
| `localhost` URL error | Use Qdrant Cloud URL, not local |
| Drive not mounted | Run cell 2 before cell 3 |

## Security

- **Never commit** notebooks with real credentials
- Use `<YOUR_QDRANT_URL>` / `<YOUR_QDRANT_API_KEY>` placeholders
- API keys in `_copy.ipynb` files are stale — those files are deleted

## Dependencies

```bash
pip install qdrant-client sentence-transformers numpy tqdm
# Colab: !pip -q install qdrant-client sentence-transformers numpy tqdm
```

## Order of Execution

Run **all three** to populate the full `medical_facts_global_1` collection:
1. Harvard (smallest, good for testing)
2. WHO (medium)
3. WebMD (largest, run last)

Each is independent — collection is shared, state files are per-source.
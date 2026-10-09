# Retriever Module - Medical Evidence Retrieval & Fine-tuning

This module provides a complete pipeline for retrieving medical evidence from Qdrant and fine-tuning a retriever model using triplet loss.

## Quick Start

### 1. Environment Setup
```bash
cd misinformation_research
pip install -r requirements.txt
# For GPU: pip install torch --index-url https://download.pytorch.org/whl/cu124
```

### 2. Configure Qdrant Connection
Create a `.env` file in `misinformation_research/`:
```bash
QDRANT_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=your-api-key
QDRANT_COLLECTION=medical_facts_global
```

### 3. Ingest Medical Sources into Qdrant
```bash
# Run from misinformation_research/ directory
python build_qdrant_incremental.py --source who
python build_qdrant_incremental.py --source webmd
python build_qdrant_incremental.py --source harvardhealth
```
This creates a collection `medical_facts_global` with dual vectors:
- `primary`: BAAI/bge-m3 (or your fine-tuned model)
- `sapbert`: cambridgeltl/SapBERT-from-PubMedBERT-fulltext

### 4. Label Claims (Evidence Annotation)
Use the Colab notebook for LLM-based labeling:
1. Open `retriever/notebooks/colab_gemma_claim_labeler.ipynb` in Google Colab (T4 GPU)
2. Set runtime to **GPU T4**
3. Upload `retriever/data/raw/claims_sample.json`
4. Run all cells → downloads `claims_llm_labeled.json`
5. Save output to `retriever/data/labeled/claims_llm_labeled.json`

### 5. Fine-tune Retriever with Triplet Loss
```bash
# From misinformation_research/ directory
python -m retriever.train_triplet_retriever
```
**Output**: `storage/models/fine_tuned_medical_retriever/`

### 6. Evaluate / Query
Open `retriever/notebooks/qdrant_query.ipynb` and set:
```python
FINE_TUNE_MODEL_PATH = "storage/models/fine_tuned_medical_retriever"
```
Run cells to test retrieval quality.

---

## Directory Structure

```
retriever/
├── config.py                    # Centralized configuration (dataclasses)
├── retrieval.py                 # Shared MedicalEvidenceRetriever class
├── train_triplet_retriever.py   # Triplet loss fine-tuning entry point
├── data/
│   ├── raw/
│   │   └── claims_sample.json          # 200 unlabeled medical claims
│   ├── labeled/
│   │   └── claims_llm_labeled.json     # LLM-labeled claims (input for training)
│   └── training/
│       └── retrieval_triplets.jsonl    # Generated triplet audit file
├── notebooks/
│   ├── qdrant_query.ipynb            # Query & evaluation notebook
│   └── colab_gemma_claim_labeler.ipynb  # LLM labeling (Colab T4)
└── outputs/                         # Logs, checkpoints (gitignored)
```

---

## Configuration

All settings are in `retriever/config.py` as frozen dataclasses. Override via:
1. **Environment variables** (`.env` file or shell):
   ```bash
   QDRANT_URL=... QDRANT_API_KEY=... python -m retriever.train_triplet_retriever
   ```
2. **Python config objects** (for programmatic use):
   ```python
   from retriever.config import TrainingConfig
   from retriever.train_triplet_retriever import main

   custom_config = TrainingConfig(
       epochs=3,
       batch_size=32,
       learning_rate=1e-5,
   )
   main(custom_config)
   ```

### Key Config Classes
| Class | Purpose | Key Parameters |
|-------|---------|----------------|
| `QdrantConfig` | Qdrant connection | `url`, `api_key`, `collection_name` |
| `ModelConfig` | Embedding models | `primary_model`, `sapbert_model`, `cross_encoder_model`, `retrieval_device` |
| `RetrievalConfig` | Retrieval pipeline | `query_pool_size`, `rerank_top_k`, `top_k`, `qdrant_weight`, `cross_encoder_weight` |
| `TrainingConfig` | Fine-tuning | `epochs`, `batch_size`, `learning_rate`, `triplet_margin`, `eval_fraction` |
| `LabelingConfig` | Human labeling (legacy) | `claims_file`, `output_file`, `wrap_width` |
| `LLMLabelingConfig` | Colab LLM labeling | `model_id`, `load_in_4bit`, `max_new_tokens` |

---

## Reproducing Results

### Exact Reproduction
1. Use the same `claims_llm_labeled.json` (commit to version control)
2. Pin dependency versions in `requirements.txt`
3. Set `RANDOM_SEED=13` (default in `TrainingConfig`)
4. Use same base model: `BAAI/bge-m3`

### Expected Outputs
- **Training triplets**: ~10,000-50,000 triplets (depends on labeled data)
- **Fine-tuned model**: ~1.3 GB (bge-m3 architecture)
- **Training time**: ~10-30 min on GPU (2 epochs, batch 16)

### Evaluation Metrics
The `TripletEvaluator` reports during training:
- `cosine_accuracy`: Fraction of triplets where `d(anchor, positive) < d(anchor, negative)`
- `triplet_loss`: TripletMarginLoss value

---

## Common Issues

| Issue | Solution |
|-------|----------|
| `QDRANT_URL not set` | Create `.env` file or export env vars |
| `Fine-tuned model path not found` | Run training first, check `storage/models/` |
| `CUDA OOM` | Reduce `batch_size` in `TrainingConfig`, use `retrieval_device="cpu"` |
| `No triplets found` | Ensure `claims_llm_labeled.json` has `positive_evidence` and `negative_evidence` arrays |
| `ImportError: qdrant_client` | `pip install qdrant-client` |

---

## For Developers

### Using the Retriever in Code
```python
from retriever.retrieval import create_retriever
from retriever.config import DEFAULT_QDRANT, DEFAULT_MODELS, DEFAULT_RETRIEVAL

retriever = create_retriever()  # Uses defaults from config
results = retriever.retrieve("Your medical claim here", top_k=10)

for r in results:
    print(f"Score: {r['overall_score']:.3f} | {r['title']} | {r['text'][:100]}...")
```

### Custom Retrieval Parameters
```python
retriever = create_retriever(
    top_k=20,
    query_pool_size=500,
    rerank_top_k=100,
    qdrant_weight=0.3,
    cross_encoder_weight=0.7,
)
```

### Extending the Pipeline
- Add new sources: Extend `build_qdrant_incremental.py` `SOURCE_CONFIGS`
- Change loss function: Modify `train_triplet_retriever.py` `train_with_triplet_loss()`
- Add evaluators: Use `sentence_transformers.evaluation` modules

---

## Files Reference

| File | Purpose |
|------|---------|
| `config.py` | All configuration, path resolution, claim/text utilities |
| `retrieval.py` | `MedicalEvidenceRetriever` class, `create_retriever()` factory |
| `train_triplet_retriever.py` | Triplet construction, TripletLoss training loop |
| `build_qdrant_incremental.py` | Incremental Qdrant ingestion (project root) |
| `colab_gemma_claim_labeler.ipynb` | LLM evidence labeling (Colab) |
| `qdrant_query.ipynb` | Ad-hoc query & analysis |

---

## License & Citation
Internal research code. See parent project for licensing.
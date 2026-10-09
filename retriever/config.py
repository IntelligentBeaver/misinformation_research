"""
Centralized configuration for the retriever module.

This module provides all configuration as frozen dataclasses with environment variable
overrides via python-dotenv (.env file in project root).

USAGE:
    from retriever.config import (
        DEFAULT_QDRANT, DEFAULT_MODELS, DEFAULT_RETRIEVAL,
        DEFAULT_TRAINING, DEFAULT_LABELING,
        DATA_RAW_DIR, DATA_LABELED_DIR, DATA_TRAINING_DIR,
        get_claims_ref, claim_text
    )

    # Use defaults
    retriever = MedicalEvidenceRetriever(
        qdrant_config=DEFAULT_QDRANT,
        model_config=DEFAULT_MODELS,
        retrieval_config=DEFAULT_RETRIEVAL,
    )

    # Or customize
    from retriever.config import TrainingConfig
    custom = TrainingConfig(epochs=4, batch_size=32, learning_rate=1e-5)

ENVIRONMENT VARIABLES (.env file):
    QDRANT_URL=https://your-cluster.qdrant.io
    QDRANT_API_KEY=your-api-key
    QDRANT_COLLECTION=medical_facts_global
    PRIMARY_RETRIEVER_MODEL=BAAI/bge-m3

DIRECTORY STRUCTURE (auto-resolved from this file's location):
    retriever/
    ├── data/raw/           -> DATA_RAW_DIR (input claims)
    ├── data/labeled/       -> DATA_LABELED_DIR (LLM/human labeled claims)
    ├── data/training/      -> DATA_TRAINING_DIR (triplet audit output)
    ├── outputs/            -> OUTPUTS_DIR (logs, checkpoints)
    └── notebooks/          -> NOTEBOOKS_DIR (Colab notebooks)

See retriever/README.md for full pipeline documentation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ============================================================================
# PATHS
# ============================================================================

RETRIEVER_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = RETRIEVER_ROOT.parent

DATA_RAW_DIR = RETRIEVER_ROOT / "data" / "raw"
DATA_LABELED_DIR = RETRIEVER_ROOT / "data" / "labeled"
DATA_TRAINING_DIR = RETRIEVER_ROOT / "data" / "training"
OUTPUTS_DIR = RETRIEVER_ROOT / "outputs"
NOTEBOOKS_DIR = RETRIEVER_ROOT / "notebooks"


def get_project_root() -> Path:
    """Resolve project root from retriever/ location."""
    return PROJECT_ROOT


# ============================================================================
# QDRANT CONFIGURATION
# ============================================================================

@dataclass(frozen=True)
class QdrantConfig:
    """Qdrant connection and collection settings."""
    url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    api_key: Optional[str] = os.getenv("QDRANT_API_KEY")
    collection_name: str = os.getenv("QDRANT_COLLECTION", "medical_facts_global")
    timeout: int = 90


# ============================================================================
# MODEL CONFIGURATION
# ============================================================================

@dataclass(frozen=True)
class ModelConfig:
    """Embedding and reranking model settings."""
    # Primary retriever model (fine-tuned BAAI/bge-m3 after training)
    primary_model: str = os.getenv("PRIMARY_RETRIEVER_MODEL", "BAAI/bge-m3")
    # SapBERT for biomedical entity linking
    sapbert_model: str = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
    # Cross-encoder for reranking
    cross_encoder_model: str = "ncbi/MedCPT-Cross-Encoder"
    # Device for retrieval models ("cpu" or "cuda")
    retrieval_device: str = "cpu"
    # Whether to use cross-encoder reranking
    use_reranker: bool = True


# ============================================================================
# RETRIEVAL CONFIGURATION
# ============================================================================

@dataclass(frozen=True)
class RetrievalConfig:
    """Retrieval pipeline parameters."""
    # Number of candidates from Qdrant per vector (before RRF fusion)
    query_pool_size: int = 200
    # Number of candidates to rerank with cross-encoder
    rerank_top_k: int = 40
    # Final number of results to return
    top_k: int = 10
    # Number of claims to process in a batch
    claim_batch_size: int = 10
    # RRF fusion weight for Qdrant scores
    qdrant_weight: float = 0.35
    # RRF fusion weight for cross-encoder scores
    cross_encoder_weight: float = 0.65
    # Cross-encoder batch size
    cross_encoder_batch_size: int = 16


# ============================================================================
# TRAINING CONFIGURATION
# ============================================================================

@dataclass(frozen=True)
class TrainingConfig:
    """Fine-tuning parameters for TripletLoss training."""
    # Base model to fine-tune
    base_model: str = "BAAI/bge-m3"
    # Training epochs
    epochs: int = 2
    # Batch size
    batch_size: int = 16
    # Warmup steps
    warmup_steps: int = 2
    # Triplet margin
    triplet_margin: float = 0.5
    # Learning rate
    learning_rate: float = 2e-5
    # Weight decay
    weight_decay: float = 0.01
    # Max gradient norm
    max_grad_norm: float = 1.0
    # Evaluation fraction
    eval_fraction: float = 0.1
    # Random seed
    random_seed: int = 13
    # Random negatives per positive (0 = all combinations)
    negatives_per_positive: int = 0
    # Output model directory
    output_model_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "storage" / "models" / "fine_tuned_medical_retriever")
    # Triplet audit file
    triplets_audit_file: Path = field(default_factory=lambda: DATA_TRAINING_DIR / "retrieval_triplets.jsonl")


# ============================================================================
# LABELING CONFIGURATION
# ============================================================================

@dataclass(frozen=True)
class LabelingConfig:
    """Interactive/LLM evidence labeling settings."""
    # Input claims file
    claims_file: Path = field(default_factory=lambda: DATA_RAW_DIR / "claims_sample.json")
    # Output file for labeled claims
    output_file: Optional[Path] = field(default_factory=lambda: DATA_LABELED_DIR / "claims_sample.human_labeled.json")
    # Whether to modify input file in place
    in_place: bool = False
    # Include already completed claims
    include_complete: bool = False
    # Start index for labeling
    start_index: int = 0
    # Text wrapping width
    wrap_width: int = 110
    # Max text chars to display (0 = full text)
    max_text_chars: int = 0


# ============================================================================
# LLM LABELING CONFIGURATION (Colab Gemma)
# ============================================================================

@dataclass(frozen=True)
class LLMLabelingConfig:
    """LLM-based evidence labeling settings (for Colab notebook)."""
    model_id: str = "google/gemma-4-E4B-it"
    load_in_4bit: bool = True
    max_new_tokens: int = 2048
    temperature: float = 0.0
    llm_claim_batch_size: int = 1
    max_evidence_chars: int = 1200
    # Resume controls
    resume_mode: bool = True
    resume_in_place: bool = True
    force_retrieve: bool = False
    force_relabel: bool = False
    include_complete: bool = False
    resume_skip_already_labeled: bool = True
    # Evidence field candidates to read from claims
    evidence_field_candidates: tuple = (
        "retrieved_evidence",
        "evidences",
        "top_evidences",
        "evidence",
        "positive_evidence",
        "negative_evidence",
    )


# ============================================================================
# CLAIM TEXT KEYS
# ============================================================================

CLAIM_TEXT_KEYS = ("claim", "text", "statement", "query")


# ============================================================================
# CONVENIENCE INSTANCES
# ============================================================================

# Default configs for easy importing
DEFAULT_QDRANT = QdrantConfig()
DEFAULT_MODELS = ModelConfig()
DEFAULT_RETRIEVAL = RetrievalConfig()
DEFAULT_TRAINING = TrainingConfig()
DEFAULT_LABELING = LabelingConfig()
DEFAULT_LLM_LABELING = LLMLabelingConfig()


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def resolve_output_path(output_file: Optional[Path], in_place: bool, source_path: Path) -> Path:
    """Resolve the output path based on config."""
    if output_file:
        return output_file.resolve()
    elif in_place:
        return source_path.resolve()
    else:
        return source_path.with_name(f"{source_path.stem}.labeled{source_path.suffix}").resolve()


def get_claims_ref(data) -> list:
    """Extract claims list from various JSON structures."""
    if isinstance(data, list):
        claims = data
    elif isinstance(data, dict) and isinstance(data.get("claims"), list):
        claims = data["claims"]
    else:
        raise ValueError("Claims JSON must be a list or an object with a 'claims' list.")

    normalized_claims = []
    for index, claim in enumerate(claims):
        if isinstance(claim, str):
            claim = {"id": f"claim_{index + 1:04d}", "claim": claim}
            claims[index] = claim
        if not isinstance(claim, dict):
            raise ValueError(f"Claim at index {index} must be an object or string.")
        normalized_claims.append(claim)

    return normalized_claims


def claim_text(claim: dict) -> str:
    """Extract claim text from a claim dictionary."""
    for key in CLAIM_TEXT_KEYS:
        value = claim.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    claim_id = claim.get("id", "<missing id>")
    raise ValueError(f"Claim {claim_id} has no text field. Expected one of: {CLAIM_TEXT_KEYS}")
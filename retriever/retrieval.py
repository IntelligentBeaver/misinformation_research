"""
Shared medical evidence retrieval logic.

This module provides the MedicalEvidenceRetriever class - the core retrieval engine
for the misinformation research pipeline.

ARCHITECTURE:
- Dual-vector Qdrant search: primary (BAAI/bge-m3) + sapbert (SapBERT)
- Reciprocal Rank Fusion (RRF) combines both vector searches
- Optional cross-encoder reranking (ncbi/MedCPT-Cross-Encoder)
- Configurable weights for fusion: qdrant_weight + cross_encoder_weight = 1.0

USAGE:
    from retriever.retrieval import create_retriever
    from retriever.config import DEFAULT_QDRANT, DEFAULT_MODELS, DEFAULT_RETRIEVAL

    # Simple usage with defaults
    retriever = create_retriever()
    results = retriever.retrieve("Metformin is first-line for type 2 diabetes", top_k=10)

    # Custom parameters
    retriever = create_retriever(
        top_k=20,
        query_pool_size=500,
        rerank_top_k=100,
        qdrant_weight=0.3,
        cross_encoder_weight=0.7,
    )

USED BY:
- retriever/notebooks/qdrant_query.ipynb (interactive query & analysis)
- retriever/notebooks/colab_gemma_claim_labeler.ipynb (LLM evidence labeling)
- retriever/train_triplet_retriever.py (can be used for evaluation)

CONFIGURATION:
All parameters controlled via retriever/config.py dataclasses:
- QdrantConfig: connection, collection, timeout
- ModelConfig: model names, device, reranker toggle
- RetrievalConfig: pool sizes, k values, fusion weights

See retriever/README.md for full pipeline documentation.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sentence_transformers import SentenceTransformer
from sentence_transformers.cross_encoder import CrossEncoder

from .config import (
    QdrantConfig,
    ModelConfig,
    RetrievalConfig,
    CLAIM_TEXT_KEYS,
    DEFAULT_QDRANT,
    DEFAULT_MODELS,
    DEFAULT_RETRIEVAL,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def min_max_normalize(values: List[float]) -> List[float]:
    if not values:
        return []

    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return [0.0 for _ in values]

    return [(value - minimum) / (maximum - minimum) for value in values]


def batched(items: List[Any], batch_size: int) -> Iterable[List[Any]]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1.")

    for index in range(0, len(items), batch_size):
        yield items[index : index + batch_size]


def evidence_text(payload: Dict[str, Any]) -> str:
    text_parts = [
        payload.get("title") or "",
        payload.get("section_heading") or "",
        payload.get("text") or "",
    ]
    return "\n".join(part for part in text_parts if part).strip()


def evidence_key(evidence: Dict[str, Any]) -> str:
    evidence_id = evidence.get("evidence_id")
    if evidence_id:
        return str(evidence_id)

    return "|".join(
        str(evidence.get(key) or "")
        for key in ("url", "title", "section_heading", "text")
    )


def compact_evidence(row: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "rank",
        "evidence_id",
        "overall_score",
        "cross_score",
        "qdrant_score",
        "qdrant_score_norm",
        "cross_score_norm",
        "source",
        "url",
        "title",
        "section_heading",
        "text",
    )
    return {key: row.get(key) for key in keys if row.get(key) is not None}


class MedicalEvidenceRetriever:
    """Multi-vector medical evidence retriever with optional cross-encoder reranking.

    Uses dual-vector Qdrant search (primary + SapBERT) with RRF fusion,
    followed by optional cross-encoder reranking.
    """

    def __init__(
        self,
        *,
        qdrant_config: Optional[QdrantConfig] = None,
        model_config: Optional[ModelConfig] = None,
        retrieval_config: Optional[RetrievalConfig] = None,
    ) -> None:
        self.qdrant_config = qdrant_config or DEFAULT_QDRANT
        self.model_config = model_config or DEFAULT_MODELS
        self.retrieval_config = retrieval_config or DEFAULT_RETRIEVAL

        try:
            from qdrant_client import QdrantClient, models as qdrant_models
        except ImportError as exc:
            raise SystemExit(
                "Missing qdrant-client. Install with: pip install qdrant-client"
            ) from exc

        self.qdrant_models = qdrant_models

        print(f"[Models] Loading primary encoder: {self.model_config.primary_model} on {self.model_config.retrieval_device}")
        self.primary_model = SentenceTransformer(
            self.model_config.primary_model,
            device=self.model_config.retrieval_device,
        )

        print(f"[Models] Loading SapBERT encoder: {self.model_config.sapbert_model} on {self.model_config.retrieval_device}")
        self.sapbert_model = SentenceTransformer(
            self.model_config.sapbert_model,
            device=self.model_config.retrieval_device,
        )

        self.cross_encoder: Optional[CrossEncoder] = None
        if self.model_config.use_reranker:
            print(f"[Models] Loading cross-encoder reranker: {self.model_config.cross_encoder_model} on {self.model_config.retrieval_device}")
            self.cross_encoder = CrossEncoder(
                self.model_config.cross_encoder_model,
                max_length=512,
                device=self.model_config.retrieval_device,
            )

        print(f"[Qdrant] Connecting to: {self.qdrant_config.url}")
        self.client = QdrantClient(
            url=self.qdrant_config.url,
            api_key=self.qdrant_config.api_key,
        )

    def retrieve(
        self,
        query: str,
        *,
        query_pool_size: Optional[int] = None,
        rerank_top_k: Optional[int] = None,
        top_k: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve evidence for a single query."""
        return self.retrieve_batch(
            [query],
            query_pool_size=query_pool_size,
            rerank_top_k=rerank_top_k,
            top_k=top_k,
            timeout=timeout,
        )[0]

    def retrieve_batch(
        self,
        queries: List[str],
        *,
        query_pool_size: Optional[int] = None,
        rerank_top_k: Optional[int] = None,
        top_k: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> List[List[Dict[str, Any]]]:
        """Retrieve evidence for multiple queries."""
        if not queries:
            return []

        qps = query_pool_size or self.retrieval_config.query_pool_size
        rtk = rerank_top_k or self.retrieval_config.rerank_top_k
        tk = top_k or self.retrieval_config.top_k
        to = timeout or self.qdrant_config.timeout

        primary_vectors = self.primary_model.encode(
            queries,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ).tolist()

        sapbert_vectors = self.sapbert_model.encode(
            queries,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ).tolist()

        qdrant_results = self._query_qdrant_batch(
            primary_vectors=primary_vectors,
            sapbert_vectors=sapbert_vectors,
            query_pool_size=qps,
            timeout=to,
        )

        rows_by_query = [
            self._rows_from_points(result.points[:rtk])
            for result in qdrant_results
        ]

        if not self.model_config.use_reranker or self.cross_encoder is None:
            return [self._top_qdrant_rows(rows, tk) for rows in rows_by_query]

        return self._rerank_batch(queries, rows_by_query, tk)

    def _query_qdrant_batch(
        self,
        *,
        primary_vectors: List[List[float]],
        sapbert_vectors: List[List[float]],
        query_pool_size: int,
        timeout: int,
    ) -> List[Any]:
        requests = [
            self.qdrant_models.QueryRequest(
                prefetch=[
                    self.qdrant_models.Prefetch(
                        query=vec_primary,
                        using="primary",
                        limit=query_pool_size,
                    ),
                    self.qdrant_models.Prefetch(
                        query=vec_sapbert,
                        using="sapbert",
                        limit=query_pool_size,
                    ),
                ],
                query=self.qdrant_models.FusionQuery(fusion=self.qdrant_models.Fusion.RRF),
                limit=query_pool_size,
                with_payload=True,
            )
            for vec_primary, vec_sapbert in zip(primary_vectors, sapbert_vectors)
        ]

        if hasattr(self.client, "query_batch_points"):
            return self.client.query_batch_points(
                collection_name=self.qdrant_config.collection_name,
                requests=requests,
                timeout=timeout,
            )

        # Compatibility fallback for older qdrant-client versions
        return [
            self.client.query_points(
                collection_name=self.qdrant_config.collection_name,
                prefetch=request.prefetch,
                query=request.query,
                limit=request.limit,
                with_payload=request.with_payload,
                timeout=timeout,
            )
            for request in requests
        ]

    def _top_qdrant_rows(self, rows: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
        for rank, row in enumerate(rows[:top_k], 1):
            row["rank"] = rank
            row["overall_score"] = row["qdrant_score"]
        return rows[:top_k]

    def _rows_from_points(self, points: Iterable[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for qdrant_rank, point in enumerate(points, 1):
            payload = point.payload or {}
            text = evidence_text(payload)
            if not text:
                continue

            rows.append(
                {
                    "qdrant_rank": qdrant_rank,
                    "evidence_id": str(point.id),
                    "title": payload.get("title"),
                    "section_heading": payload.get("section_heading"),
                    "text": payload.get("text"),
                    "qdrant_score": float(point.score),
                    "source": payload.get("source_site"),
                    "url": payload.get("url"),
                }
            )

        return rows

    def _rerank(self, query: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rerank_texts = [
            "\n".join(
                part
                for part in (
                    row.get("title") or "",
                    row.get("section_heading") or "",
                    row.get("text") or "",
                )
                if part
            ).strip()
            for row in rows
        ]

        cross_scores = self.cross_encoder.predict(
            [[query, text] for text in rerank_texts],
            batch_size=self.retrieval_config.cross_encoder_batch_size,
            show_progress_bar=False,
        )

        qdrant_scores = [float(row["qdrant_score"]) for row in rows]
        cross_scores = [float(score) for score in cross_scores]

        qdrant_scores_norm = min_max_normalize(qdrant_scores)
        cross_scores_norm = min_max_normalize(cross_scores)

        reranked_results: List[Dict[str, Any]] = []
        for row, qdrant_norm, cross_norm, cross_score in zip(
            rows,
            qdrant_scores_norm,
            cross_scores_norm,
            cross_scores,
        ):
            row["qdrant_score_norm"] = float(qdrant_norm)
            row["cross_score_norm"] = float(cross_norm)
            row["cross_score"] = float(cross_score)
            row["overall_score"] = (
                self.retrieval_config.qdrant_weight * row["qdrant_score_norm"]
            ) + (
                self.retrieval_config.cross_encoder_weight * row["cross_score_norm"]
            )
            reranked_results.append(row)

        reranked_results.sort(key=lambda item: item["overall_score"], reverse=True)
        for rank, row in enumerate(reranked_results, 1):
            row["rank"] = rank

        return reranked_results

    def _rerank_batch(
        self,
        queries: List[str],
        rows_by_query: List[List[Dict[str, Any]]],
        top_k: int,
    ) -> List[List[Dict[str, Any]]]:
        pairs: List[List[str]] = []
        pair_refs: List[Tuple[int, int]] = []

        for query_index, (query, rows) in enumerate(zip(queries, rows_by_query)):
            for row_index, row in enumerate(rows):
                text = "\n".join(
                    part
                    for part in (
                        row.get("title") or "",
                        row.get("section_heading") or "",
                        row.get("text") or "",
                    )
                    if part
                ).strip()
                if not text:
                    continue

                pairs.append([query, text])
                pair_refs.append((query_index, row_index))

        if not pairs:
            return [[] for _ in rows_by_query]

        cross_scores = self.cross_encoder.predict(
            pairs,
            batch_size=self.retrieval_config.cross_encoder_batch_size,
            show_progress_bar=False,
        )

        for (query_index, row_index), cross_score in zip(pair_refs, cross_scores):
            rows_by_query[query_index][row_index]["cross_score"] = float(cross_score)

        reranked_by_query: List[List[Dict[str, Any]]] = []
        for rows in rows_by_query:
            scored_rows = [row for row in rows if row.get("cross_score") is not None]
            if not scored_rows:
                reranked_by_query.append([])
                continue

            qdrant_scores = [float(row["qdrant_score"]) for row in scored_rows]
            cross_scores_for_query = [float(row["cross_score"]) for row in scored_rows]

            qdrant_scores_norm = min_max_normalize(qdrant_scores)
            cross_scores_norm = min_max_normalize(cross_scores_for_query)

            for row, qdrant_norm, cross_norm in zip(
                scored_rows,
                qdrant_scores_norm,
                cross_scores_norm,
            ):
                row["qdrant_score_norm"] = float(qdrant_norm)
                row["cross_score_norm"] = float(cross_norm)
                row["overall_score"] = (
                    self.retrieval_config.qdrant_weight * row["qdrant_score_norm"]
                ) + (
                    self.retrieval_config.cross_encoder_weight * row["cross_score_norm"]
                )

            scored_rows.sort(key=lambda item: item["overall_score"], reverse=True)
            for rank, row in enumerate(scored_rows, 1):
                row["rank"] = rank

            reranked_by_query.append(scored_rows[:top_k])

        return reranked_by_query

    def get_retrieval_metadata(self) -> Dict[str, Any]:
        """Get metadata about the current retrieval configuration."""
        return {
            "collection": self.qdrant_config.collection_name,
            "query_pool_size": self.retrieval_config.query_pool_size,
            "rerank_top_k": self.retrieval_config.rerank_top_k,
            "top_k": self.retrieval_config.top_k,
            "claim_batch_size": self.retrieval_config.claim_batch_size,
            "qdrant_weight": self.retrieval_config.qdrant_weight,
            "cross_encoder_weight": self.retrieval_config.cross_encoder_weight,
            "reranker": self.model_config.cross_encoder_model if self.model_config.use_reranker else None,
            "retrieved_at": utc_now_iso(),
        }


def create_retriever(
    qdrant_url: Optional[str] = None,
    qdrant_api_key: Optional[str] = None,
    collection_name: Optional[str] = None,
    primary_model: Optional[str] = None,
    sapbert_model: Optional[str] = None,
    cross_encoder_model: Optional[str] = None,
    use_reranker: Optional[bool] = None,
    retrieval_device: Optional[str] = None,
    query_pool_size: Optional[int] = None,
    rerank_top_k: Optional[int] = None,
    top_k: Optional[int] = None,
    claim_batch_size: Optional[int] = None,
    qdrant_weight: Optional[float] = None,
    cross_encoder_weight: Optional[float] = None,
    cross_encoder_batch_size: Optional[int] = None,
) -> MedicalEvidenceRetriever:
    """Factory function to create a retriever with optional overrides."""
    qdrant_config = QdrantConfig(
        url=qdrant_url or DEFAULT_QDRANT.url,
        api_key=qdrant_api_key or DEFAULT_QDRANT.api_key,
        collection_name=collection_name or DEFAULT_QDRANT.collection_name,
    )
    model_config = ModelConfig(
        primary_model=primary_model or DEFAULT_MODELS.primary_model,
        sapbert_model=sapbert_model or DEFAULT_MODELS.sapbert_model,
        cross_encoder_model=cross_encoder_model or DEFAULT_MODELS.cross_encoder_model,
        retrieval_device=retrieval_device or DEFAULT_MODELS.retrieval_device,
        use_reranker=use_reranker if use_reranker is not None else DEFAULT_MODELS.use_reranker,
    )
    retrieval_config = RetrievalConfig(
        query_pool_size=query_pool_size or DEFAULT_RETRIEVAL.query_pool_size,
        rerank_top_k=rerank_top_k or DEFAULT_RETRIEVAL.rerank_top_k,
        top_k=top_k or DEFAULT_RETRIEVAL.top_k,
        claim_batch_size=claim_batch_size or DEFAULT_RETRIEVAL.claim_batch_size,
        qdrant_weight=qdrant_weight or DEFAULT_RETRIEVAL.qdrant_weight,
        cross_encoder_weight=cross_encoder_weight or DEFAULT_RETRIEVAL.cross_encoder_weight,
        cross_encoder_batch_size=cross_encoder_batch_size or DEFAULT_RETRIEVAL.cross_encoder_batch_size,
    )
    return MedicalEvidenceRetriever(
        qdrant_config=qdrant_config,
        model_config=model_config,
        retrieval_config=retrieval_config,
    )
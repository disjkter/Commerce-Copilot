import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

from .catalog import HybridSearchHit, Product
from .database import CatalogRepository, get_catalog


class Vectorizer(Protocol):
    def encode(self, text: str) -> list[float]: ...


class FeatureHashVectorizer:
    """Dependency-free vectorizer used as the offline retrieval baseline."""

    def __init__(self, dimensions: int = 512):
        self.dimensions = dimensions

    def encode(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % self.dimensions
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class HybridRetriever:
    def __init__(
        self,
        repository: CatalogRepository,
        vectorizer: Vectorizer | None = None,
        *,
        rrf_k: int = 60,
        min_vector_score: float = 0.08,
    ):
        self.repository = repository
        self.vectorizer = vectorizer or FeatureHashVectorizer()
        self.rrf_k = rrf_k
        self.min_vector_score = min_vector_score

    def search(
        self,
        query: str,
        *,
        max_price_cents: int | None = None,
        categories: list[str] | None = None,
        in_stock_only: bool = True,
        limit: int = 5,
        recall_limit: int = 20,
    ) -> list[HybridSearchHit]:
        lexical_hits = self.repository.search_products(
            query,
            max_price_cents=max_price_cents,
            categories=categories,
            in_stock_only=in_stock_only,
            limit=recall_limit,
        )
        candidates = self.repository.list_products(
            max_price_cents=max_price_cents,
            categories=categories,
            in_stock_only=in_stock_only,
        )

        lexical_ranks = {
            hit.product.product_id: rank
            for rank, hit in enumerate(lexical_hits, start=1)
        }
        lexical_categories = {hit.product.category for hit in lexical_hits}
        query_vector = self.vectorizer.encode(query)
        vector_scores = [
            (product.product_id, _cosine(query_vector, self.vectorizer.encode(_product_text(product))))
            for product in candidates
            if not lexical_categories or product.category in lexical_categories
        ]
        vector_scores = [item for item in vector_scores if item[1] >= self.min_vector_score]
        vector_scores.sort(key=lambda item: (-item[1], item[0]))
        vector_ranks = {
            product_id: rank
            for rank, (product_id, _) in enumerate(vector_scores[:recall_limit], start=1)
        }

        products_by_id = {product.product_id: product for product in candidates}
        candidate_ids = lexical_ranks.keys() | vector_ranks.keys()
        hits: list[HybridSearchHit] = []
        for product_id in candidate_ids:
            lexical_rank = lexical_ranks.get(product_id)
            vector_rank = vector_ranks.get(product_id)
            fused_score = 0.0
            sources: list[str] = []
            if lexical_rank is not None:
                fused_score += 1 / (self.rrf_k + lexical_rank)
                sources.append("fts5")
            if vector_rank is not None:
                fused_score += 1 / (self.rrf_k + vector_rank)
                sources.append("vector")
            hits.append(
                HybridSearchHit(
                    product=products_by_id[product_id],
                    fused_score=fused_score,
                    lexical_rank=lexical_rank,
                    vector_rank=vector_rank,
                    retrieval_sources=sources,
                )
            )

        hits.sort(
            key=lambda hit: (
                -hit.fused_score,
                -hit.product.rating,
                -hit.product.review_count,
                hit.product.product_id,
            )
        )
        return hits[:limit]


def _tokens(text: str) -> list[str]:
    lowered = text.casefold()
    tokens = re.findall(r"[a-z][a-z0-9_-]+", lowered)
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _product_text(product: Product) -> str:
    return " ".join(
        (
            product.title,
            product.category,
            product.brand,
            product.description,
            " ".join(product.tags),
        )
    )


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


@lru_cache
def get_retriever() -> HybridRetriever:
    return HybridRetriever(get_catalog())

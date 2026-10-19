import argparse
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

from .database import CatalogRepository, get_catalog
from .retrieval import HybridRetriever, get_retriever


@dataclass(frozen=True)
class RetrievalCase:
    case_id: str
    query: str
    relevant_product_ids: frozenset[str]
    max_price_cents: int | None = None
    in_stock_only: bool = True


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    query: str
    expected: tuple[str, ...]
    retrieved: tuple[str, ...]
    recall_at_k: float
    reciprocal_rank: float


@dataclass(frozen=True)
class EvaluationResult:
    retriever: str
    k: int
    case_count: int
    recall_at_k: float
    hit_rate_at_k: float
    mrr: float
    cases: tuple[CaseResult, ...]


DEFAULT_CASES = (
    RetrievalCase(
        case_id="commute-headphones",
        query="通勤无线耳机",
        relevant_product_ids=frozenset({"CP-AUD-101", "CP-AUD-102"}),
    ),
    RetrievalCase(
        case_id="ambient-awareness",
        query="希望长时间佩戴还能感知周围环境",
        relevant_product_ids=frozenset({"CP-AUD-103"}),
    ),
    RetrievalCase(
        case_id="quiet-keyboard",
        query="办公室静音机械键盘",
        relevant_product_ids=frozenset({"CP-KEY-201"}),
    ),
    RetrievalCase(
        case_id="budget-headphones",
        query="预算四百元以内的通勤无线耳机",
        relevant_product_ids=frozenset({"CP-AUD-101"}),
        max_price_cents=40000,
    ),
    RetrievalCase(
        case_id="mobile-mouse",
        query="移动办公轻量无线鼠标",
        relevant_product_ids=frozenset({"CP-MOU-301"}),
    ),
)


SearchFunction = Callable[[RetrievalCase, int], Sequence[str]]


def evaluate(
    name: str,
    search: SearchFunction,
    cases: Sequence[RetrievalCase] = DEFAULT_CASES,
    *,
    k: int = 3,
) -> EvaluationResult:
    if k < 1:
        raise ValueError("k must be at least 1")
    if not cases:
        raise ValueError("at least one evaluation case is required")
    if any(not case.relevant_product_ids for case in cases):
        raise ValueError("every evaluation case needs a relevant product")

    case_results: list[CaseResult] = []
    for case in cases:
        retrieved = tuple(search(case, k)[:k])
        matching = case.relevant_product_ids.intersection(retrieved)
        recall = len(matching) / len(case.relevant_product_ids)
        first_relevant_rank = next(
            (
                rank
                for rank, product_id in enumerate(retrieved, start=1)
                if product_id in case.relevant_product_ids
            ),
            None,
        )
        case_results.append(
            CaseResult(
                case_id=case.case_id,
                query=case.query,
                expected=tuple(sorted(case.relevant_product_ids)),
                retrieved=retrieved,
                recall_at_k=recall,
                reciprocal_rank=(1 / first_relevant_rank if first_relevant_rank else 0.0),
            )
        )

    total = len(case_results)
    return EvaluationResult(
        retriever=name,
        k=k,
        case_count=total,
        recall_at_k=sum(item.recall_at_k for item in case_results) / total,
        hit_rate_at_k=sum(item.reciprocal_rank > 0 for item in case_results) / total,
        mrr=sum(item.reciprocal_rank for item in case_results) / total,
        cases=tuple(case_results),
    )


def compare_retrievers(
    repository: CatalogRepository,
    hybrid: HybridRetriever,
    cases: Sequence[RetrievalCase] = DEFAULT_CASES,
    *,
    k: int = 3,
) -> tuple[EvaluationResult, EvaluationResult]:
    def lexical_search(case: RetrievalCase, limit: int) -> list[str]:
        return [
            hit.product.product_id
            for hit in repository.search_products(
                case.query,
                max_price_cents=case.max_price_cents,
                in_stock_only=case.in_stock_only,
                limit=limit,
            )
        ]

    def hybrid_search(case: RetrievalCase, limit: int) -> list[str]:
        return [
            hit.product.product_id
            for hit in hybrid.search(
                case.query,
                max_price_cents=case.max_price_cents,
                in_stock_only=case.in_stock_only,
                limit=limit,
            )
        ]

    return (
        evaluate("fts5", lexical_search, cases, k=k),
        evaluate("hybrid_rrf", hybrid_search, cases, k=k),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate product retrieval offline.")
    parser.add_argument("--k", type=int, default=3, help="Evaluate the top K results.")
    parser.add_argument(
        "--details",
        action="store_true",
        help="Include every query and retrieved product ID.",
    )
    args = parser.parse_args()

    results = compare_retrievers(get_catalog(), get_retriever(), k=args.k)
    payload = []
    for result in results:
        item = asdict(result)
        if not args.details:
            item.pop("cases")
        payload.append(item)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

import pytest

from commerce_copilot.database import CatalogRepository
from commerce_copilot.evaluation import RetrievalCase, compare_retrievers, evaluate
from commerce_copilot.retrieval import HybridRetriever


def test_evaluate_calculates_recall_hit_rate_and_mrr():
    cases = (
        RetrievalCase("first", "query one", frozenset({"A", "B"})),
        RetrievalCase("second", "query two", frozenset({"C"})),
    )
    rankings = {
        "first": ["A", "X", "B"],
        "second": ["X", "C", "Y"],
    }

    result = evaluate(
        "fake",
        lambda case, k: rankings[case.case_id][:k],
        cases,
        k=2,
    )

    assert result.recall_at_k == pytest.approx(0.75)
    assert result.hit_rate_at_k == pytest.approx(1.0)
    assert result.mrr == pytest.approx(0.75)
    assert result.cases[0].retrieved == ("A", "X")


def test_evaluate_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="k must"):
        evaluate("fake", lambda case, k: [], k=0)
    with pytest.raises(ValueError, match="at least one"):
        evaluate("fake", lambda case, k: [], (), k=3)
    empty_relevance = (RetrievalCase("empty", "query", frozenset()),)
    with pytest.raises(ValueError, match="relevant product"):
        evaluate("fake", lambda case, k: [], empty_relevance, k=3)


def test_hybrid_retrieval_improves_default_offline_benchmark(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()
    lexical, hybrid = compare_retrievers(repository, HybridRetriever(repository), k=3)

    assert hybrid.recall_at_k > lexical.recall_at_k
    assert hybrid.hit_rate_at_k > lexical.hit_rate_at_k
    assert hybrid.mrr >= lexical.mrr

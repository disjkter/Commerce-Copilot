from commerce_copilot.database import CatalogRepository
from commerce_copilot.retrieval import FeatureHashVectorizer, HybridRetriever


def make_retriever(tmp_path) -> HybridRetriever:
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()
    return HybridRetriever(repository)


def test_feature_hash_vectorizer_is_deterministic():
    vectorizer = FeatureHashVectorizer(dimensions=128)

    first = vectorizer.encode("通勤无线耳机")
    second = vectorizer.encode("通勤无线耳机")

    assert first == second
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9


def test_vector_channel_recalls_description_when_fts_has_no_terms(tmp_path):
    retriever = make_retriever(tmp_path)

    hits = retriever.search("希望长时间佩戴还能感知周围环境")

    assert hits
    assert hits[0].product.product_id == "CP-AUD-103"
    assert hits[0].lexical_rank is None
    assert hits[0].retrieval_sources == ["vector"]


def test_rrf_combines_fts_and_vector_ranks(tmp_path):
    retriever = make_retriever(tmp_path)

    hits = retriever.search("通勤无线耳机")

    assert hits
    assert hits[0].product.product_id in {"CP-AUD-101", "CP-AUD-102"}
    assert hits[0].retrieval_sources == ["fts5", "vector"]


def test_hard_budget_filter_applies_to_both_channels(tmp_path):
    retriever = make_retriever(tmp_path)

    hits = retriever.search("通勤无线耳机", max_price_cents=40000)

    assert [hit.product.product_id for hit in hits] == ["CP-AUD-101"]


def test_hard_category_filter_applies_to_both_channels(tmp_path):
    retriever = make_retriever(tmp_path)

    hits = retriever.search(
        "我要耳机 办公",
        max_price_cents=30000,
        categories=["耳机"],
    )

    assert hits == []

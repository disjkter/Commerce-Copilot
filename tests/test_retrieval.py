from commerce_copilot.database import PRODUCT_EMBEDDING_DIMENSIONS, CatalogRepository
from commerce_copilot.retrieval import (
    BGEVectorizer,
    FeatureHashVectorizer,
    HybridRetriever,
)


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


def test_default_vectorizer_matches_postgres_embedding_dimension():
    vector = FeatureHashVectorizer().encode("通勤无线耳机")

    assert len(vector) == PRODUCT_EMBEDDING_DIMENSIONS


def test_feature_hash_model_id_includes_dimensions():
    assert FeatureHashVectorizer(128).model_id == "feature-hash-v1-128"


def test_bge_vectorizer_batches_and_normalizes_embeddings():
    class FakeVector:
        def astype(self, data_type):
            assert data_type is float
            return self

        def tolist(self):
            return [0.25, 0.75]

    class FakeModel:
        def encode(self, texts, **options):
            assert texts == ["query", "product"]
            assert options["normalize_embeddings"] is True
            assert options["convert_to_numpy"] is True
            assert options["show_progress_bar"] is False
            return [FakeVector(), FakeVector()]

    vectorizer = BGEVectorizer()
    vectorizer._model = FakeModel()

    assert vectorizer.model_id == "sentence-transformers:BAAI/bge-m3"
    assert vectorizer.encode_many(["query", "product"]) == [
        [0.25, 0.75],
        [0.25, 0.75],
    ]


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

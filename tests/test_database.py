from commerce_copilot.database import CatalogRepository


def test_seed_is_idempotent(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")

    repository.seed_products()
    repository.seed_products()

    assert repository.count_products() == 5


def test_fts_search_returns_relevant_products(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()

    hits = repository.search_products("想买通勤用的无线耳机")

    assert hits
    assert all(hit.product.category == "耳机" for hit in hits)
    assert hits[0].product.product_id in {"CP-AUD-101", "CP-AUD-102"}


def test_budget_is_a_hard_filter(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()

    hits = repository.search_products("蓝牙耳机", max_price_cents=40000)

    assert [hit.product.product_id for hit in hits] == ["CP-AUD-101"]


def test_unknown_term_returns_no_result(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.seed_products()

    assert repository.search_products("咖啡机") == []


def test_conversation_turns_are_returned_in_chronological_order(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.db")
    repository.add_conversation_turn("session-a", "第一问", "第一答")
    repository.add_conversation_turn("session-a", "第二问", "第二答")
    repository.add_conversation_turn("session-b", "其他问题", "其他回答")

    turns = repository.get_recent_conversation_turns("session-a")

    assert [turn.user_message for turn in turns] == ["第一问", "第二问"]
    assert [turn.assistant_answer for turn in turns] == ["第一答", "第二答"]
